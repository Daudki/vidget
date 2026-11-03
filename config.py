import os
from urllib.parse import urlsplit

ENGINE_KEYS = ('video', 'files', 'photos')
PROXY_SCHEMES = ('http', 'https', 'socks5', 'socks5h')


def _load_proxy():
    raw = os.environ.get('PROXY_URL', '').strip()
    if not raw:
        return '', None
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme not in PROXY_SCHEMES or not parts.hostname or not port:
        raise ValueError('PROXY_URL must look like socks5h://host:port or http://user:pass@host:port')
    return raw, (parts.hostname.lower(), port)


def _load_proxy_engines():
    raw = os.environ.get('PROXY_ENGINES', '')
    chosen = {item.strip() for item in raw.split(',') if item.strip()} or set(ENGINE_KEYS)
    unknown = chosen - set(ENGINE_KEYS)
    if unknown:
        raise ValueError('PROXY_ENGINES has unknown entries: ' + ', '.join(sorted(unknown)))
    return chosen


PROXY_URL, PROXY_ADDRESS = _load_proxy()
PROXY_ENGINES = _load_proxy_engines()


def proxy_for(engine_key):
    return PROXY_URL if PROXY_URL and engine_key in PROXY_ENGINES else ''


def requests_proxies(engine_key):
    url = proxy_for(engine_key)
    return {'http': url, 'https': url} if url else None


def is_proxy_address(host, port):
    return PROXY_ADDRESS is not None and (host.strip('[]').lower(), port) == PROXY_ADDRESS


def redact(text):
    if not PROXY_URL or not text:
        return text
    parts = urlsplit(PROXY_URL)
    for secret in (PROXY_URL, parts.netloc, parts.hostname):
        if secret:
            text = text.replace(secret, '[proxy]')
    return text
