#!/usr/bin/env python3
import os
import json
import re
import signal
import sys
import time
from datetime import datetime, timezone

import requests

# =========================
# Configuracion por entorno
# =========================
#
# Este script se controla casi por completo con variables de entorno.
# La idea es que el contenedor pueda cambiar comportamiento sin tocar el codigo.
#
# OPENCTI_URL / OPENCTI_TOKEN:
#   Identifican donde vive OpenCTI y con que credencial se conecta el builder.
# STREAM_ID:
#   Si viene vacio, el script usa el stream general; si no, apunta a uno especifico.
# FEEDS_DIR:
#   Carpeta donde se escriben ip.txt, url.txt, hash.txt y state.json.
#
# El resto de variables ajustan filtros, rendimiento, backfill y retencion.
# =========================
OPENCTI_URL = os.environ.get("OPENCTI_URL", "http://opencti:8080").rstrip("/")
OPENCTI_TOKEN = os.environ.get("OPENCTI_TOKEN", "").strip()

STREAM_ID = os.environ.get("STREAM_ID", "").strip()

FEEDS_DIR = os.environ.get("FEEDS_DIR", "/feeds").rstrip("/")
MIN_SCORE = int(os.environ.get("MIN_SCORE", "50"))
MIN_CONFIDENCE = int(os.environ.get("MIN_CONFIDENCE", "50"))
ALLOW_TLP = set([x.strip().lower() for x in os.environ.get("ALLOW_TLP", "tlp:clear,tlp:green").split(",") if x.strip()])

REQUIRE_DETECTION = os.environ.get("REQUIRE_DETECTION", "false").strip().lower() in ("1", "true", "yes")
STATS_EVERY_SEC = int(os.environ.get("STATS_EVERY_SEC", "60"))
PUBLIC_FEEDS_BASE_URL = os.environ.get("PUBLIC_FEEDS_BASE_URL", "").strip().rstrip("/")
PROCESS_OBSERVABLE_HASHES = os.environ.get("PROCESS_OBSERVABLE_HASHES", "true").strip().lower() in ("1", "true", "yes")
WRITE_INTERVAL_SEC = int(os.environ.get("WRITE_INTERVAL_SEC", "3600"))
HASH_FROM_ANY_EVENT = os.environ.get("HASH_FROM_ANY_EVENT", "false").strip().lower() in ("1", "true", "yes")
HASH_ONLY_SHA256 = os.environ.get("HASH_ONLY_SHA256", "true").strip().lower() in ("1", "true", "yes")
HASH_RELAX_FILTERS = os.environ.get("HASH_RELAX_FILTERS", "true").strip().lower() in ("1", "true", "yes")
BACKFILL_ENABLED = os.environ.get("BACKFILL_ENABLED", "true").strip().lower() in ("1", "true", "yes")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "30"))
BACKFILL_PAGE_SIZE = int(os.environ.get("BACKFILL_PAGE_SIZE", "200"))
BACKFILL_MAX_PAGES = int(os.environ.get("BACKFILL_MAX_PAGES", "50"))

MAX_AGE_DAYS_IP = int(os.environ.get("MAX_AGE_DAYS_IP", "15"))
MAX_AGE_DAYS_URL = int(os.environ.get("MAX_AGE_DAYS_URL", "30"))
MAX_AGE_DAYS_HASH = int(os.environ.get("MAX_AGE_DAYS_HASH", "60"))

# Debug: si quieres ver 1 evento crudo y luego apagarlo
DEBUG_DUMP_ONCE = os.environ.get("DEBUG_DUMP_ONCE", "false").strip().lower() in ("1", "true", "yes")

# Limites de tamano SSE (AUDITORIA.md P1): sin esto, una linea o un evento
# anormalmente grande puede agotar memoria del proceso.
MAX_SSE_LINE_BYTES = int(os.environ.get("MAX_SSE_LINE_BYTES", str(256 * 1024)))
MAX_SSE_EVENT_BYTES = int(os.environ.get("MAX_SSE_EVENT_BYTES", str(2 * 1024 * 1024)))

# Healthcheck (AUDITORIA.md P2): senal de actividad en disco, consultable por
# "docker healthcheck" desde otro proceso corto que no comparte memoria con el builder.
HEARTBEAT_FILE = os.path.join(FEEDS_DIR, ".heartbeat")
HEALTHCHECK_MAX_AGE_SEC = int(os.environ.get("HEALTHCHECK_MAX_AGE_SEC", "600"))

IP_FILE = os.path.join(FEEDS_DIR, "ip.txt")
URL_FILE = os.path.join(FEEDS_DIR, "url.txt")
HASH_FILE = os.path.join(FEEDS_DIR, "hash.txt")
STATE_FILE = os.path.join(FEEDS_DIR, "state.json")

HEADERS = {
    "Authorization": f"Bearer {OPENCTI_TOKEN}",
    "Accept": "text/event-stream",
}

HASH_CANDIDATE_RE = re.compile(r"(?i)(?<![a-f0-9])([a-f0-9]{64})(?![a-f0-9])")

# =========================
# Helpers y utilidades
# =========================
def log(msg: str):
    # Registro consistente con timestamp UTC para poder seguir el flujo en logs.
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[feed-builder] {ts} {msg}", flush=True)

def now_utc():
    return datetime.now(timezone.utc)

def iso_to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def ensure_dir():
    # Crea la carpeta de feeds si aun no existe.
    os.makedirs(FEEDS_DIR, exist_ok=True)

def init_feed_file(path, title):
    # Inicializa el archivo con metadatos legibles para saber de donde salio.
    if os.path.exists(path):
        return
    stamp = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n")
        f.write("# OpenCTI Threat Feed (generated)\n")
        f.write(f"# Last updated: {stamp}\n")
        f.write(f"# Policy: revoked=false, valid_until>=now, score>={MIN_SCORE}, confidence>={MIN_CONFIDENCE}\n")
        if ALLOW_TLP:
            f.write(f"# TLP allowlist (labels): {','.join(sorted(ALLOW_TLP))} (if no tlp label present => allowed)\n")
        f.write("# Format: 1 indicator per line\n\n")

def load_state():
    # Estado persistente del builder.
    # seen_ids: evita reprocesar el mismo STIX ID.
    # seen_values: recuerda el valor del IOC y hasta cuando es valido.
    # pending: cola temporal cuando el modo no escribe de inmediato.
    default_state = {"seen_ids": [], "seen_values": {}, "pending": {"ip": {}, "url": {}, "hash": {}}, "_migrated_now": True}
    if not os.path.exists(STATE_FILE):
        return default_state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            data.setdefault("seen_ids", [])
            
            # Migración: Si seen_values es una lista (formato antiguo), lo inicializa a un dict vacío para forzar a releer.
            if not isinstance(data.get("seen_values"), dict):
                data["seen_values"] = {}
                data["_migrated_now"] = True
                
            data.setdefault("pending", {"ip": {}, "url": {}, "hash": {}})
            for key in ("ip", "url", "hash"):
                if not isinstance(data["pending"].get(key), dict):
                    data["pending"][key] = {}
            return data
    except Exception:
        return default_state

def save_state(state):
    # Guardado atomico del estado para no romper el JSON si el proceso se corta.
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def safe_get_indicator_extension(ind: dict) -> dict:
    # OpenCTI puede guardar datos extra en distintas extensiones STIX.
    # Primero intentamos encontrar una extension_type util; si no, devolvemos la primera que exista.
    exts = ind.get("extensions") or {}
    for _, v in exts.items():
        if isinstance(v, dict) and v.get("extension_type") == "property-extension":
            return v
    for _, v in exts.items():
        if isinstance(v, dict):
            return v
    return {}

def bump(counter: dict, key: str):
    # Suma 1 a la estadistica pedida.
    counter[key] = counter.get(key, 0) + 1

def to_int(value, default=0):
    # Convierte a entero sin lanzar excepciones hacia arriba.
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default

def to_bool(value, default=False):
    # Interpreta strings y booleanos de forma tolerante.
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default

def tlp_allowed(ind: dict) -> bool:
    # Si el indicador trae etiquetas TLP, solo pasa si alguna coincide con la allowlist.
    # Si no trae TLP, el script no lo bloquea por este motivo.
    if not ALLOW_TLP:
        return True
    labels = [str(x).lower() for x in (ind.get("labels") or [])]
    tlps = [l for l in labels if l.startswith("tlp:")]
    if not tlps:
        # si no viene tlp en labels, no lo bloquees (marcaje puede venir por markings)
        return True
    return any(tlp in ALLOW_TLP for tlp in tlps)

def get_indicator_age_days(ind: dict) -> int:
    # Usa la primera marca de tiempo disponible para estimar antiguedad del indicador.
    created = iso_to_dt(ind.get("created_at") or ind.get("created") or ind.get("updated_at") or ind.get("modified"))
    if not created:
        return 0
    return (now_utc() - created).days

def indicator_base_allowed(ind: dict, feed_type: str = None, stats: dict = None) -> bool:
    # Filtros base comunes a todo IOC.
    # Este bloque decide primero lo que es claramente invalido antes de mirar score/confianza.
    if ind.get("revoked") is True:
        if stats is not None:
            bump(stats, "drop_revoked")
        return False

    vu = iso_to_dt(ind.get("valid_until"))
    if vu and vu < now_utc():
        if stats is not None:
            bump(stats, "drop_expired")
        return False

    # Revisar Edad / TTL
    age = get_indicator_age_days(ind)
    if feed_type == "ip" and age > MAX_AGE_DAYS_IP:
        if stats is not None: bump(stats, "drop_ttl_ip")
        return False
    if feed_type == "url" and age > MAX_AGE_DAYS_URL:
        if stats is not None: bump(stats, "drop_ttl_url")
        return False
    if feed_type == "hash" and age > MAX_AGE_DAYS_HASH:
        if stats is not None: bump(stats, "drop_ttl_hash")
        return False

    if not tlp_allowed(ind):
        if stats is not None:
            bump(stats, "drop_tlp")
        return False

    return True

def valid_indicator(ind: dict, feed_type: str = None, stats: dict = None) -> bool:
    # Filtro completo del indicador.
    # Primero valida reglas de vida util/TLP y luego la calidad del IOC.
    if not indicator_base_allowed(ind, feed_type=feed_type, stats=stats):
        return False

    ext = safe_get_indicator_extension(ind)
    # score y confidence pueden venir en diferentes campos segun el conector o la forma STIX.
    score = to_int(ext.get("score"), default=None)
    if score is None:
        score = to_int(ind.get("x_opencti_score"), default=None)
    if score is None:
        score = to_int(ind.get("x_opencti_score_norm"), default=0)

    conf = to_int(ind.get("confidence"), default=None)
    if conf is None:
        conf = to_int(ind.get("x_opencti_confidence"), default=0)

    detection = to_bool(ext.get("detection"), default=None)
    if detection is None:
        detection = to_bool(ind.get("x_opencti_detection"), default=False)

    if score < MIN_SCORE:
        if stats is not None:
            bump(stats, "drop_score")
        return False
    if conf < MIN_CONFIDENCE:
        if stats is not None:
            bump(stats, "drop_confidence")
        return False
    if REQUIRE_DETECTION and not detection:
        if stats is not None:
            bump(stats, "drop_detection")
        return False

    return True

def extract_observable(ind: dict):
    # Intenta reconstruir el observable real desde varias fuentes porque OpenCTI no siempre
    # entrega el mismo formato para todos los indicadores.
    ext = safe_get_indicator_extension(ind)

    # Caso ideal: la extension ya trae el valor observable estructurado.
    ovs = ext.get("observable_values") or []
    if isinstance(ovs, list) and len(ovs) > 0 and isinstance(ovs[0], dict):
        t = ovs[0].get("type")
        v = ovs[0].get("value")
        if t and v:
            return str(t), str(v).strip()

    pattern = ind.get("pattern") or ""
    # Caso STIX clasico: extraer value = '...'
    m = re.search(r"value\s*=\s*['\"]([^'\"]+)['\"]", pattern)
    if m:
        v = m.group(1).strip()
        t = ext.get("main_observable_type") or ind.get("x_opencti_main_observable_type") or "Unknown"
        return str(t), v

    # patrones STIX comunes para hash en File objects
    m_hash = re.search(r"hashes\.[^=]+\s*=\s*['\"]([^'\"]+)['\"]", pattern, flags=re.IGNORECASE)
    if m_hash:
        return "file", m_hash.group(1).strip()

    # fallback: selecciona el token entre comillas que parece IOC.
    # Esto rescata casos donde el pattern trae el valor, pero no en el formato exacto esperado.
    quoted = [x.strip() for x in re.findall(r"['\"]([^'\"]+)['\"]", pattern)]
    for token in reversed(quoted):
        if re.fullmatch(r"[A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}|[A-Fa-f0-9]{128}", token):
            return "file", token
        if token.startswith("http://") or token.startswith("https://"):
            return "url", token
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", token):
            return "ipv4-addr", token

    # fallback: toma cualquier valor entre comillas del pattern si no encontró "value = ..."
    m2 = re.search(r"['\"]([^'\"]+)['\"]", pattern)
    if m2:
        v = m2.group(1).strip()
        t = ext.get("main_observable_type") or ind.get("x_opencti_main_observable_type") or "Unknown"
        return str(t), v

    name = (ind.get("name") or "").strip()
    if not name:
        return None, None
    t = ext.get("main_observable_type") or ind.get("x_opencti_main_observable_type") or "Unknown"
    return str(t), name

def classify(observable_type: str, value: str):
    # Traduce el observable a una de las tres salidas del proyecto.
    # Todo lo que no calza en ip/url/hash se descarta.
    if not value:
        return None

    t = (observable_type or "").lower()

    if t in ("ipv4-addr", "ipv6-addr"):
        return "ip"
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value):
        return "ip"
    if ":" in value and re.fullmatch(r"[0-9a-fA-F:]+", value):
        return "ip"

    if t == "url" or value.startswith("http://") or value.startswith("https://"):
        return "url"

    if re.fullmatch(r"[A-Fa-f0-9]{64}", value):
        return "hash"

    return None

def is_hash_value(value: str) -> bool:
    # Por defecto solo aceptamos SHA-256; eso reduce ruido y evita mezclar hashes de distinto tipo.
    if not value:
        return False
    if HASH_ONLY_SHA256:
        return bool(re.fullmatch(r"[A-Fa-f0-9]{64}", value.strip()))
    return bool(re.fullmatch(r"[A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}|[A-Fa-f0-9]{128}", value.strip()))

def extract_hash_candidates(text: str):
    # Busca candidatos SHA-256 dentro de cualquier string.
    if not isinstance(text, str):
        return []
    return [m.group(1).lower() for m in HASH_CANDIDATE_RE.finditer(text)]

def normalize_hash(value: str):
    # Normaliza hashes escritos como sha256:ABC... o SHA-256=ABC...
    if not isinstance(value, str):
        return None
    v = value.strip().strip("'\"").lower()
    v = re.sub(r"^(md5|sha1|sha-1|sha256|sha-256|sha512|sha-512)\s*[:=]\s*", "", v)
    if is_hash_value(v):
        return v
    candidates = extract_hash_candidates(v)
    return candidates[0] if candidates else None

def iter_string_values(obj, depth=0, max_depth=6):
    # Recorrido recursivo liviano para encontrar hashes ocultos en cualquier parte del payload.
    if depth > max_depth:
        return
    if isinstance(obj, str):
        yield obj
        return
    if isinstance(obj, dict):
        for _, v in obj.items():
            yield from iter_string_values(v, depth + 1, max_depth)
        return
    if isinstance(obj, list):
        for item in obj:
            yield from iter_string_values(item, depth + 1, max_depth)

def extract_hash_values_from_observable(stix: dict):
    # Extrae hashes desde un observable STIX, soportando varias formas de serializacion.
    hashes = []

    # STIX file object shape: {"type":"file", "hashes": {"SHA-256":"..."}}
    hv = stix.get("hashes")
    if isinstance(hv, dict):
        for _, v in hv.items():
            n = normalize_hash(v)
            if n:
                hashes.append(n)
    elif isinstance(hv, list):
        for item in hv:
            if isinstance(item, dict):
                n = normalize_hash(item.get("hash") or item.get("value"))
                if n:
                    hashes.append(n)

    # Algunos conectores dejan hashes en campos sueltos
    for field in ("md5", "sha1", "sha256", "sha512", "hash", "observable_value", "name"):
        n = normalize_hash(stix.get(field))
        if n:
            hashes.append(n)

    # fallback desde pattern si vino como string STIX-like
    pattern = stix.get("pattern") or ""
    for token in re.findall(r"['\"]([^'\"]+)['\"]", pattern):
        n = normalize_hash(token)
        if n:
            hashes.append(n)

    # fallback profundo: busca hashes en cualquier string del objeto.
    # Esto es mas caro, pero permite rescatar datos cuando el formato viene incompleto.
    for raw_text in iter_string_values(stix):
        for candidate in extract_hash_candidates(raw_text):
            hashes.append(candidate)

    # uniq preserving order
    out = []
    seen = set()
    for h in hashes:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out

def extract_hash_values_from_any_payload(payload_obj: dict):
    hashes = []
    for raw_text in iter_string_values(payload_obj):
        for candidate in extract_hash_candidates(raw_text):
            hashes.append(candidate)

    out = []
    seen = set()
    for h in hashes:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out

def append_line(path: str, value: str):
    # Escritura simple de una linea en el feed.
    with open(path, "a", encoding="utf-8") as f:
        f.write(value + "\n")

def calculate_expiration(feed: str, value: str, ttl_days: int) -> int:
    # TTL relativo desde ahora.
    return int(time.time()) + (ttl_days * 86400)

def queue_or_write(feed: str, value: str, pending: dict, seen_values: dict, stats: dict, force_now: bool = False, payload_dt: datetime = None):
    # Centro de control de escritura.
    # Decide entre escribir inmediatamente o guardar primero en la cola pending.
    immediate = force_now or WRITE_INTERVAL_SEC <= 0

    if feed == "ip":
        ttl_days = MAX_AGE_DAYS_IP
    elif feed == "url":
        ttl_days = MAX_AGE_DAYS_URL
    else:
        ttl_days = MAX_AGE_DAYS_HASH

    # Determinar cuando expira este indicador específico (desde su payload o desde ahora)
    base_ts = payload_dt.timestamp() if payload_dt else time.time()
    exp_ts = int(base_ts) + (ttl_days * 86400)

    # Si ya expiró ni siquiera lo encolamos
    if time.time() > exp_ts:
        # Esto evita que IOC viejos entren al feed.
        bump(stats, f"drop_ttl_{feed}")
        return 0

    if immediate:
        # Modo inmediato: el archivo se reconstruye al vuelo con el estado actual.
        if value in seen_values and seen_values[value] >= exp_ts:
            bump(stats, "duplicate_value")
            return 0
            
        # Actualizamos la expiración en seen_values
        seen_values[value] = exp_ts
        
        # En modo inmediate reescribimos el archivo entero para ese feed
        path = IP_FILE if feed == "ip" else (URL_FILE if feed == "url" else HASH_FILE)
        rebuild_feed_file(feed, path, seen_values)
        
        bump(stats, f"write_{feed}")
        bump(stats, "writes_total")
        log(f"WRITE/REBUILD {feed}: {value} (expires: {datetime.fromtimestamp(exp_ts, timezone.utc).isoformat()})")
        return 1

    # Modo batch: acumulamos valores y luego los vaciamos en flush_pending().
    bucket = pending.get(feed)
    if bucket is None:
        return 0
        
    if value in seen_values and seen_values[value] >= exp_ts:
        bump(stats, "duplicate_value")
        return 0
    if value in bucket and bucket[value] >= exp_ts:
        # Ya estaba pendiente con una expiracion equivalente o mejor.
        bump(stats, "duplicate_value")
        return 0

    bucket[value] = exp_ts
    bump(stats, f"queued_{feed}")
    bump(stats, "queued_total")
    return 1

def rebuild_feed_file(feed: str, path: str, seen_values: dict):
    # Reconstruye el archivo completo dejando solo valores vigentes.
    #
    # Ojo: seen_values mezcla ip/url/hash en un solo diccionario, por eso no guardamos
    # un indice separado por tipo. En vez de eso, usamos heuristicas para decidir
    # a que archivo pertenece cada valor al reconstruir.
    
    tmp = path + ".tmp"
    init_feed_file(tmp, f"{feed.upper()} feed")
    
    now = time.time()
    count = 0
    with open(tmp, "a", encoding="utf-8") as f:
        for val, exp_ts in seen_values.items():
            if exp_ts < now:
                continue # expirado, no debe volver al feed
                
            # Verificacion por forma del dato para decidir si corresponde al feed actual.
            if feed == "hash" and is_hash_value(val):
                f.write(val + "\n")
                count += 1
            elif feed == "ip" and classify("ipv4-addr", val) == "ip":
                f.write(val + "\n")
                count += 1
            elif feed == "url" and classify("url", val) == "url":
                f.write(val + "\n")
                count += 1
    os.replace(tmp, path)
    return count

def flush_pending(pending: dict, seen_values: dict, stats: dict, force_rebuild_all: bool = False):
    # Vacía la cola pendiente hacia seen_values y luego reconstruye los feeds afectados.
    # Esto es lo que evita reescribir por cada evento cuando WRITE_INTERVAL_SEC es mayor que 0.
    wrote = 0
    now = time.time()
    
    for feed, path in (("ip", IP_FILE), ("url", URL_FILE), ("hash", HASH_FILE)):
        bucket = pending.get(feed) or {}
        
        fed_wrote = 0
        if bucket:
            for value, exp_ts in bucket.items():
                if exp_ts < now:
                    continue
                if value in seen_values and seen_values[value] >= exp_ts:
                    continue
                seen_values[value] = exp_ts
                fed_wrote += 1
                wrote += 1
                bump(stats, f"write_{feed}")
                bump(stats, "writes_total")
                log(f"QUEUED->SEEN {feed}: {value} (expires: {datetime.fromtimestamp(exp_ts, timezone.utc).isoformat()})")
                
            pending[feed] = {}
        
        # Reconstruir si hubo cambios para este feed o si forzamos reconstrucción general.
        if fed_wrote > 0 or force_rebuild_all:
            rebuild_feed_file(feed, path, seen_values)

    if wrote:
        bump(stats, "flush_runs")
        bump(stats, "flush_written_total")
    return wrote

def compute_next_flush_ts(pending: dict):
    # Planificador simple para el siguiente flush.
    # No intenta ser exacto por cola; solo marca un proximo punto de limpieza.
    if WRITE_INTERVAL_SEC <= 0:
        return time.time()
    return time.time() + max(1, WRITE_INTERVAL_SEC)

def sse_size_check(event_data_parts: list, new_line: bytes):
    # Verifica una nueva linea SSE contra los limites de tamano antes de acumularla.
    # Devuelve (True, None) si la linea puede agregarse, o (False, reason) si debe
    # descartarse (y con ella, el evento completo al que pertenece).
    if len(new_line) > MAX_SSE_LINE_BYTES:
        return False, "drop_sse_line_too_large"
    projected = sum(len(p) for p in event_data_parts) + len(new_line)
    if projected > MAX_SSE_EVENT_BYTES:
        return False, "drop_sse_event_too_large"
    return True, None


_shutdown_requested = False


def request_shutdown(signum=None, frame=None):
    # Manejador de SIGTERM/SIGINT (AUDITORIA.md P2): solo marca la bandera,
    # el bucle principal es quien decide cuando es seguro cortar (cooperativo).
    global _shutdown_requested
    _shutdown_requested = True


def shutdown_requested() -> bool:
    return _shutdown_requested


def write_heartbeat(path: str = None):
    # Escritura atomica, igual que save_state, para no dejar el archivo a medias.
    p = path or HEARTBEAT_FILE
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(repr(time.time()))
    os.replace(tmp, p)


def heartbeat_age_seconds(path: str = None):
    p = path or HEARTBEAT_FILE
    try:
        with open(p, "r", encoding="utf-8") as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except Exception:
        return None


def is_healthy(max_age_seconds: int = None, path: str = None) -> bool:
    max_age = HEALTHCHECK_MAX_AGE_SEC if max_age_seconds is None else max_age_seconds
    age = heartbeat_age_seconds(path)
    return age is not None and age <= max_age


def stream_url():
    # Si se define STREAM_ID, se apunta a un stream concreto; si no, al stream general.
    if STREAM_ID:
        return f"{OPENCTI_URL}/stream/{STREAM_ID}"
    return f"{OPENCTI_URL}/stream"

def log_feed_targets():
    # Ayuda de diagnostico: muestra las rutas locales y la URL publica esperada.
    log(f"Feed files local: ip={IP_FILE} url={URL_FILE} hash={HASH_FILE}")
    if PUBLIC_FEEDS_BASE_URL:
        log(
            "Feed URLs: "
            f"ip={PUBLIC_FEEDS_BASE_URL}/ip.txt "
            f"url={PUBLIC_FEEDS_BASE_URL}/url.txt "
            f"hash={PUBLIC_FEEDS_BASE_URL}/hash.txt"
        )

def normalize_payload(obj):
    """
    OpenCTI SSE a veces manda:
    - dict directo
    - string con JSON dentro
    """
    # Normaliza ambos casos para que el resto del flujo trabaje con un dict.
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        s = obj.strip()
        # intenta decodificar JSON embebido
        try:
            return json.loads(s)
        except Exception:
            return {"_raw": s}
    return {"_raw": str(obj)}

def extract_stix(payload: dict):
    """
    Normaliza dónde viene el STIX:
    - payload["data"]["data"]
    - payload["data"]
    - payload directo (si ya es STIX)
    """
    # OpenCTI cambia el envoltorio segun el origen del evento, por eso hay varios caminos.
    d = payload.get("data")
    if isinstance(d, dict):
        dd = d.get("data")
        if isinstance(dd, dict):
            return dd
        # a veces data ya es STIX
        if d.get("type") in ("indicator", "observable", "stix-core-object", "stix-domain-object", "stix-cyber-observable"):
            return d
    # payload podría ser directamente el stix
    if payload.get("type") in ("indicator", "observable", "stix-core-object", "stix-domain-object", "stix-cyber-observable"):
        return payload
    return None

def post_graphql(query: str, variables: dict):
    # Cliente minimo para backfill por GraphQL.
    url = f"{OPENCTI_URL}/graphql"
    headers = {
        "Authorization": f"Bearer {OPENCTI_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"query": query, "variables": variables}
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data.get('errors')}")
    return data.get("data") or {}

def extract_nodes(connection_obj):
    # Convierte una respuesta GraphQL con edges/pageInfo a una lista de nodos.
    if not isinstance(connection_obj, dict):
        return [], None, False

    edges = connection_obj.get("edges") or []
    nodes = []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                nodes.append(edge["node"])

    page = connection_obj.get("pageInfo") or {}
    end_cursor = page.get("endCursor")
    has_next = bool(page.get("hasNextPage"))
    return nodes, end_cursor, has_next

def backfill_last_days_hashes(seen_values: set, pending: dict, stats: dict):
    # Backfill inicial: busca indicadores recientes y trata de extraer hashes historicos.
    # Se usa solo si el modo de backfill esta habilitado.
    if not BACKFILL_ENABLED or BACKFILL_DAYS <= 0:
        return

    query = """
    query BackfillIndicators($first: Int!, $after: ID) {
      indicators(first: $first, after: $after, orderBy: created_at, orderMode: desc) {
        edges {
          node {
            id
            name
            pattern
            created_at
            updated_at
          }
        }
        pageInfo {
          endCursor
          hasNextPage
        }
      }
    }
    """

    threshold = time.time() - (BACKFILL_DAYS * 86400)
    cursor = None
    page_count = 0
    total_nodes = 0
    total_hashes = 0

    log(f"Backfill start: indicators from last {BACKFILL_DAYS} days")

    while page_count < max(1, BACKFILL_MAX_PAGES):
        variables = {"first": max(1, BACKFILL_PAGE_SIZE), "after": cursor}
        try:
            data = post_graphql(query, variables)
        except Exception as e:
            log(f"BACKFILL_ERROR: {e}")
            break

        conn = data.get("indicators") or {}
        nodes, end_cursor, has_next = extract_nodes(conn)
        if not nodes:
            break

        page_count += 1
        total_nodes += len(nodes)

        older_reached = False
        for node in nodes:
            when = iso_to_dt(node.get("updated_at") or node.get("created_at"))
            if when is not None and when.timestamp() < threshold:
                # Cuando llegamos a indicadores demasiado antiguos, cortamos la exploracion.
                older_reached = True
                continue

            text_blob = json.dumps(node, ensure_ascii=False)
            hashes = extract_hash_candidates(text_blob)
            if not hashes:
                continue

            for h in hashes:
                # Backfill usa la misma pipeline que el stream normal.
                total_hashes += queue_or_write("hash", h, pending, seen_values, stats, payload_dt=when)

        if older_reached:
            break
        if not has_next:
            break
        cursor = end_cursor
        if not cursor:
            break

    log(f"Backfill done: pages={page_count} indicators={total_nodes} hashes_written_or_queued={total_hashes}")

def process_sse_event_data(data_part: bytes, seen_ids: set, seen_values: set, pending: dict, debug_state: dict, stats: dict) -> int:
    """
    Procesa el payload de UN evento SSE (data concatenada).
    Retorna 1 si escribió algo, 0 si no.
    """
    # Cada bloque SSE completo se procesa como una unidad independiente.
    data_part = (data_part or b"").strip()
    if not data_part:
        bump(stats, "empty_events")
        return 0

    # 1er decode JSON
    try:
        obj = json.loads(data_part)
    except Exception:
        bump(stats, "non_json_events")
        # data no era JSON; en modo debug deja una pista 1 vez
        if DEBUG_DUMP_ONCE and not debug_state.get("non_json_dumped"):
            log("DEBUG_DUMP_ONCE non-JSON SSE data example (truncated):")
            try:
                log(data_part.decode("utf-8", errors="replace")[:600])
            except Exception:
                pass
            debug_state["non_json_dumped"] = True
        return 0

    payload = normalize_payload(obj)
    wrote = 0

    # Dump controlado 1 vez para ver formato real
    if DEBUG_DUMP_ONCE and not debug_state.get("dumped"):
        log("DEBUG_DUMP_ONCE payload example (truncated):")
        try:
            s = json.dumps(payload, ensure_ascii=False)
            log(s[:1200] + ("..." if len(s) > 1200 else ""))
        except Exception:
            log(str(payload)[:1200])
        debug_state["dumped"] = True

    if HASH_FROM_ANY_EVENT and isinstance(payload, dict):
        # Esta rama busca hashes aunque el evento no sea un indicator puro.
        # Sirve para rescatar IOC escondidos en otros payloads OpenCTI.
        raw_hashes = extract_hash_values_from_any_payload(payload)
        if raw_hashes:
            for h in raw_hashes:
                wrote += queue_or_write("hash", h, pending, seen_values, stats)
            if wrote:
                bump(stats, "hash_from_any_event")

    stix = extract_stix(payload)
    if not isinstance(stix, dict):
        bump(stats, "no_stix_events")
        return wrote

    stix_type = str(stix.get("type") or "").lower()
    if stix_type != "indicator":
        # Si no es indicator, solo procesamos hashes de observables cuando la configuracion lo permite.
        if not PROCESS_OBSERVABLE_HASHES:
            bump(stats, "non_indicator_events")
            return wrote

        wrote = 0
        for h in extract_hash_values_from_observable(stix):
            wrote += queue_or_write("hash", h, pending, seen_values, stats)

        if wrote > 0:
            bump(stats, "observable_hash_events")
            return wrote

        bump(stats, "non_indicator_events")
        return 0

    ind_id = stix.get("id") or ""
    # Dedupe por ID: si OpenCTI repite el mismo indicador, no lo volvemos a escribir.
    if ind_id and ind_id in seen_ids:
        bump(stats, "duplicate_id")
        return 0

    otype, oval = extract_observable(stix)
    feed = classify(otype, oval)
    if not feed:
        # Si no logramos clasificarlo en ip/url/hash, se descarta.
        bump(stats, "drop_unclassified")
        if ind_id:
            seen_ids.add(ind_id)
        return 0

    # Los hashes pueden relajar algunos filtros para no perder IOC utiles.
    if feed == "hash" and HASH_RELAX_FILTERS:
        if not indicator_base_allowed(stix, feed_type=feed, stats=stats):
            if ind_id:
                seen_ids.add(ind_id)
            return 0
    else:
        if not valid_indicator(stix, feed_type=feed, stats=stats):
            if ind_id:
                seen_ids.add(ind_id)
            return 0

    stix_dt = iso_to_dt(stix.get("created_at") or stix.get("created") or stix.get("updated_at") or stix.get("modified"))
    # La fecha del STIX se usa como base para calcular el TTL del valor publicado.
    wrote = queue_or_write(feed, oval, pending, seen_values, stats, payload_dt=stix_dt)
    if ind_id:
        seen_ids.add(ind_id)
    return wrote

def maybe_log_stats(stats: dict, force: bool = False):
    # Emite un resumen de salud del flujo cada cierto tiempo.
    now = time.time()
    if not force and now < stats.get("next_log_ts", 0):
        return

    snapshot_keys = (
        "events_total",
        "non_json_events",
        "no_stix_events",
        "non_indicator_events",
        "hash_from_any_event",
        "observable_hash_events",
        "drop_revoked",
        "drop_expired",
        "drop_tlp",
        "drop_score",
        "drop_confidence",
        "drop_detection",
        "drop_unclassified",
        "duplicate_id",
        "duplicate_value",
        "queued_total",
        "queued_ip",
        "queued_url",
        "queued_hash",
        "writes_total",
        "write_ip",
        "write_url",
        "write_hash",
        "flush_runs",
        "flush_written_total",
        "event_parse_error",
    )
    parts = []
    for key in snapshot_keys:
        value = stats.get(key, 0)
        if value:
            parts.append(f"{key}={value}")

    if not parts:
        # Aunque no haya actividad, conviene dejar una traza minima del proceso vivo.
        parts = ["events_total=0"]

    log("STATS " + " ".join(parts))
    stats["next_log_ts"] = now + max(10, STATS_EVERY_SEC)

def listen_forever():
    # Loop principal del builder.
    # Hace bootstrap, conecta al stream, procesa eventos y se reconecta si algo falla.
    if not OPENCTI_TOKEN:
        raise RuntimeError("OPENCTI_TOKEN is not set")

    # Preparacion del directorio y de los archivos de salida.
    ensure_dir()
    init_feed_file(IP_FILE, "IP feed")
    init_feed_file(URL_FILE, "URL feed")
    init_feed_file(HASH_FILE, "SHA256 feed")
    log_feed_targets()

    # Apagado ordenado (AUDITORIA.md P2): SIGTERM/SIGINT solo marcan la bandera,
    # el bucle principal hace flush+compact+save antes de salir.
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    # Heartbeat inicial para que el healthcheck no falle durante el arranque/backfill.
    write_heartbeat()

    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    seen_values = state.get("seen_values", {})
    pending = {
        "ip": dict((state.get("pending") or {}).get("ip") or {}),
        "url": dict((state.get("pending") or {}).get("url") or {}),
        "hash": dict((state.get("pending") or {}).get("hash") or {}),
    }

    if state.get("_migrated_now"):
        # Si hubo cambio de formato de estado, se fuerza una limpieza de feeds viejos.
        log("Migration detected: Clearing legacy feed .txt files so they don't contain expired indicators")
        for temp_path, title in ((IP_FILE, "IP feed"), (URL_FILE, "URL feed"), (HASH_FILE, "SHA256 feed")):
            if os.path.exists(temp_path):
                os.remove(temp_path)
            init_feed_file(temp_path, title)
        # Quitar el flag de migración para no hacerlo la próxima vez
        if "_migrated_now" in state:
            del state["_migrated_now"]
            save_state(state)

    def compact():
        # Compacta memoria interna y persiste el estado en disco.
        # Esto evita que seen_values y seen_ids crezcan sin control.
        nonlocal seen_ids, seen_values, state, pending
        now = time.time()
        
        # Purga por timeout
        purged = 0
        alive_values = {}
        for val, exp_ts in seen_values.items():
            if exp_ts >= now:
                # Solo sobreviven los valores aun validos.
                alive_values[val] = exp_ts
            else:
                purged += 1
        seen_values = alive_values

        if len(seen_ids) > 50000:
            # Recorte defensivo para que la memoria no crezca sin limite.
            seen_ids = set(list(seen_ids)[-30000:])
            
        if purged > 0:
            log(f"Compacted dictionary, removed {purged} expired seen values.")

        state["seen_ids"] = list(seen_ids)
        state["seen_values"] = seen_values
        state["pending"] = {
            "ip": pending.get("ip") or {},
            "url": pending.get("url") or {},
            "hash": pending.get("hash") or {},
        }
        save_state(state)

    backoff = 2
    debug_state = {"dumped": False, "non_json_dumped": False}
    stats = {"next_log_ts": time.time() + max(10, STATS_EVERY_SEC)}
    next_flush_ts = compute_next_flush_ts(pending)

    if WRITE_INTERVAL_SEC > 0:
        # Batch mode: acumula y vacia cada cierto intervalo.
        log(f"Batch mode enabled: flushing accumulated values every {WRITE_INTERVAL_SEC}s")
        log(
            "Pending queue loaded: "
            f"ip={len(pending.get('ip') or {})} "
            f"url={len(pending.get('url') or {})} "
            f"hash={len(pending.get('hash') or {})}"
        )
    else:
        log("Immediate mode enabled: writing values as soon as they arrive")
        pending_total = len(pending.get("ip") or {}) + len(pending.get("url") or {}) + len(pending.get("hash") or {})
        if pending_total > 0:
            # Si existian IOC pendientes en disco, se escriben al arrancar.
            flushed = flush_pending(pending, seen_values, stats)
            compact()
            log(f"Immediate mode startup flush: {flushed} IOC written from pending queue")

    backfill_last_days_hashes(seen_values, pending, stats)
    
    # Reescritura total al arranque.
    # Con esto se limpia cualquier contenido viejo que haya quedado en los txt.
    flushed = flush_pending(pending, seen_values, stats, force_rebuild_all=True)
    log(f"Startup immediate flush (forced rebuild): {flushed} IOC newly processed, all files rebuilt.")
    compact()

    while not shutdown_requested():
        # Reintento infinito con backoff: si OpenCTI cae o el stream se corta, vuelve solo.
        url = stream_url()
        log(f"Connecting SSE: {url} (MIN_SCORE={MIN_SCORE} MIN_CONF={MIN_CONFIDENCE} REQUIRE_DETECTION={REQUIRE_DETECTION})")
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
                if r.status_code == 401:
                    raise RuntimeError("401 Unauthorized (token invalid?)")
                r.raise_for_status()

                backoff = 2
                wrote = 0

                event_data_parts = []
                event_oversized = False
                for raw in r.iter_lines():
                    if raw is None:
                        continue

                    # Fin de evento SSE: línea vacía
                    if raw == b"":
                        if event_oversized:
                            # Evento descartado por tamano (AUDITORIA.md P1): no se procesa parcial.
                            bump(stats, "dropped_oversized_events")
                            event_data_parts = []
                            event_oversized = False
                        elif event_data_parts:
                            # Cada evento completo se arma juntando las lineas data: recibidas.
                            payload = b"\n".join(event_data_parts)
                            try:
                                bump(stats, "events_total")
                                wrote += process_sse_event_data(payload, seen_ids, seen_values, pending, debug_state, stats)
                                if wrote and wrote % 20 == 0:
                                    # Compactamos cada cierto volumen para sostener el proceso largo.
                                    compact()
                            except Exception as ee:
                                bump(stats, "event_parse_error")
                                log(f"EVENT_PARSE_ERROR: {ee}")
                            event_data_parts = []
                        maybe_log_stats(stats)
                        write_heartbeat()

                        if WRITE_INTERVAL_SEC > 0 and time.time() >= next_flush_ts:
                            # Flush periodico en modo batch.
                            flushed = flush_pending(pending, seen_values, stats)
                            if flushed:
                                compact()
                                log(f"FLUSH completed: {flushed} IOC written")
                            next_flush_ts = time.time() + max(1, WRITE_INTERVAL_SEC)

                        if shutdown_requested():
                            log("Shutdown requested, closing stream cooperatively")
                            break
                        continue

                    # Comentarios SSE
                    if raw.startswith(b":"):
                        continue

                    # Sólo acumulamos líneas data:
                    if raw.startswith(b"data:") and not event_oversized:
                        line = raw[5:].lstrip()
                        ok, reason = sse_size_check(event_data_parts, line)
                        if not ok:
                            bump(stats, reason)
                            log(f"SSE_SIZE_LIMIT: {reason}, discarding current event")
                            event_oversized = True
                            event_data_parts = []
                            continue
                        event_data_parts.append(line)

                # Si el stream cierra sin línea vacía final, procesa el último evento
                if event_data_parts and not event_oversized:
                    try:
                        bump(stats, "events_total")
                        wrote += process_sse_event_data(b"\n".join(event_data_parts), seen_ids, seen_values, pending, debug_state, stats)
                    except Exception as ee:
                        bump(stats, "event_parse_error")
                        log(f"EVENT_PARSE_ERROR: {ee}")

                if WRITE_INTERVAL_SEC > 0 and time.time() >= next_flush_ts:
                    # Si el stream termina justo antes del flush, se conserva igual.
                    flushed = flush_pending(pending, seen_values, stats)
                    if flushed:
                        log(f"FLUSH completed: {flushed} IOC written")
                    next_flush_ts = time.time() + max(1, WRITE_INTERVAL_SEC)

                compact()
                maybe_log_stats(stats, force=True)

        except Exception as e:
            # Cualquier error rompe temporalmente la conexion y luego se intenta de nuevo.
            log(f"ERROR: {e} (reconnect in {backoff}s)")
            if WRITE_INTERVAL_SEC > 0:
                flushed = flush_pending(pending, seen_values, stats)
                if flushed:
                    compact()
                    log(f"FLUSH completed after error: {flushed} IOC written")
            maybe_log_stats(stats, force=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    log("Shutdown: final flush, compact and save before exit")
    flush_pending(pending, seen_values, stats, force_rebuild_all=True)
    compact()
    log("Shutdown complete")

def main():
    if "--healthcheck" in sys.argv:
        sys.exit(0 if is_healthy() else 1)
    listen_forever()

if __name__ == "__main__":
    main()
