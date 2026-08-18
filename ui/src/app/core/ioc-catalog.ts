// Refleja hub/models.py Family + FAMILY_SUBTYPES (catalogo cerrado, ver
// docstring alli sobre por que no es un enum abierto). Etiquetas en espanol
// para el operador, mismo criterio que hub/models.py usa para Family.
export type IocFamily = 'hash' | 'network' | 'web' | 'identity' | 'content' | 'vulnerability' | 'custom';

export const FAMILY_LABELS: Record<IocFamily, string> = {
  hash: 'Hash',
  network: 'Red',
  web: 'Web',
  identity: 'Identidad',
  content: 'Contenido',
  vulnerability: 'Vulnerabilidad',
  custom: 'Custom',
};

// Family.CUSTOM no aparece aca: no tiene catalogo cerrado (subtipo
// registrado por adaptador), se agrega como texto libre en la UI.
export const FAMILY_SUBTYPES: Record<Exclude<IocFamily, 'custom'>, string[]> = {
  hash: [
    'md5', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512',
    'sha3-256', 'sha3-512', 'ssdeep', 'tlsh', 'imphash',
    'authentihash', 'pehash', 'custom-hash',
  ],
  network: ['ipv4', 'ipv6', 'cidr', 'mac-address', 'asn'],
  web: ['url', 'domain', 'hostname', 'fqdn', 'uri', 'user-agent'],
  identity: ['email', 'username', 'phone'],
  content: ['keyword', 'file-name', 'mutex', 'registry-key', 'process-name', 'service-name'],
  vulnerability: ['cve', 'cwe'],
};
