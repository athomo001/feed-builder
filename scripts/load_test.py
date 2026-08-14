#!/usr/bin/env python3
"""Load test simple contra el Admin API. Concurrencia via
`concurrent.futures.ThreadPoolExecutor` + `requests` (sin dependencia
nueva) -- no reemplaza una herramienta de carga real (k6/locust/gatling)
para un ambiente de staging serio, pero alcanza para una prueba rapida de
regresion de latencia contra una instancia local o de desarrollo.

Uso:
    python scripts/load_test.py --base-url http://localhost:8000/admin/api/v1 \\
        --token <bearer-token> --endpoint /destinations --requests 200 --concurrency 20

Autor: Athan Espinoza
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def _one_request(url: str, headers: dict, timeout: float) -> float:
    # perf_counter() (reloj monotonico) en vez de time.time(), para que un
    # ajuste del reloj de sistema durante la corrida no distorsione la
    # latencia medida.
    start = time.perf_counter()
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return time.perf_counter() - start


def _percentile(sorted_values: list, p: float) -> float:
    # Percentil aproximado por indice (sin interpolacion): suficiente para
    # un reporte rapido de load test, no hace falta la precision de una
    # libreria de estadistica.
    if not sorted_values:
        return 0.0
    # min(...) evita un IndexError cuando p=1.0 empuja el indice justo mas
    # alla del ultimo elemento de la lista.
    index = min(len(sorted_values) - 1, int(len(sorted_values) * p))
    return sorted_values[index]


def run_load_test(
    *, base_url: str, endpoint: str, token: str, total_requests: int, concurrency: int, timeout: float = 10.0
) -> dict:
    url = f"{base_url.rstrip('/')}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    latencies = []
    errors = 0
    started_at = time.perf_counter()
    # `max_workers=concurrency` es lo que efectivamente simula la carga
    # concurrente: todas las requests se encolan de una vez y el pool las
    # ejecuta en paralelo hasta ese limite.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one_request, url, headers, timeout) for _ in range(total_requests)]
        for future in as_completed(futures):
            try:
                latencies.append(future.result())
            except Exception:
                # Una request individual que falla no debe abortar todo el
                # load test; se cuenta como error y se sigue midiendo el
                # resto.
                errors += 1
    total_seconds = time.perf_counter() - started_at

    latencies.sort()
    return {
        "total_requests": total_requests,
        "concurrency": concurrency,
        "errors": errors,
        "total_seconds": total_seconds,
        "requests_per_second": (total_requests / total_seconds) if total_seconds else 0.0,
        "p50_seconds": _percentile(latencies, 0.50),
        "p95_seconds": _percentile(latencies, 0.95),
        "p99_seconds": _percentile(latencies, 0.99),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000/admin/api/v1")
    parser.add_argument("--endpoint", default="/destinations")
    parser.add_argument("--token", default="")
    parser.add_argument("--requests", type=int, default=100, dest="total_requests")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    result = run_load_test(
        base_url=args.base_url,
        endpoint=args.endpoint,
        token=args.token,
        total_requests=args.total_requests,
        concurrency=args.concurrency,
        timeout=args.timeout,
    )
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
