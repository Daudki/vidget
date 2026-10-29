import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import tempfile
import time
from urllib.parse import unquote, urljoin, urlsplit

import requests
import urllib3.util.connection as u3conn
from werkzeug.http import parse_options_header

import jobstore
from .base import Engine

MAX_BYTES = int(os.environ.get('MAX_FILE_MB', '1024')) * 1024 * 1024
MAX_SECONDS = int(os.environ.get('MAX_FILE_SECONDS', '900'))
MAX_REDIRECTS = 5
CHUNK = 256 * 1024
TIMEOUT = (10, 60)
REDIRECT_CODES = (301, 302, 303, 307, 308)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; VidGet/1.0)',
    'Accept-Encoding': 'identity',
}

session = requests.Session()
session.trust_env = False


class FetchError(Exception):
    pass


def resolve_public(host):
    host = host.strip('[]')
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError('Could not resolve the host')
    chosen = None
    for info in infos:
        raw = info[4][0].split('%')[0]
        addr = ipaddress.ip_address(raw)
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if not addr.is_global:
            raise FetchError('That address is not allowed')
        if chosen is None:
            chosen = raw
    if chosen is None:
        raise FetchError('Could not resolve the host')
    return chosen


_original_create_connection = u3conn.create_connection


def guarded_create_connection(address, *args, **kwargs):
    host, port = address
    return _original_create_connection((resolve_public(host), port), *args, **kwargs)


u3conn.create_connection = guarded_create_connection


def human_bytes(n):
    value = float(n)
    for unit in ('B', 'KB', 'MB'):
        if value < 1024:
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.2f} GB'


def human_eta(seconds):
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes:02d}:{secs:02d}'


def sanitize(name):
    name = os.path.basename((name or '').replace('\\', '/')).strip()
    name = re.sub(r'[^\w.\- ]', '_', name).strip(' .')
    return name[:150]


def open_stream(url, headers=None):
    current = url
    merged = {**HEADERS, **(headers or {})}
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise FetchError('Only http and https links are supported')
        resp = session.get(
            current,
            stream=True,
            allow_redirects=False,
            timeout=TIMEOUT,
            headers=merged,
        )
        if resp.status_code in REDIRECT_CODES:
            location = resp.headers.get('Location')
            resp.close()
            if not location:
                raise FetchError('The server sent an invalid redirect')
            current = urljoin(current, location)
            continue
        return resp, current
    raise FetchError('Too many redirects')


def looks_like_page(resp):
    content_type = resp.headers.get('Content-Type', '').lower()
    disposition = resp.headers.get('Content-Disposition', '').lower()
    return content_type.startswith('text/html') and 'attachment' not in disposition


def server_filename(resp, final_url):
    header = resp.headers.get('Content-Disposition', '')
    if header:
        _, options = parse_options_header(header)
        candidate = sanitize(options.get('filename', ''))
        if candidate:
            return candidate
    return sanitize(unquote(urlsplit(final_url).path))


def extension_from_type(content_type):
    mime = content_type.split(';')[0].strip().lower()
    if not mime or mime == 'application/octet-stream':
        return ''
    return mimetypes.guess_extension(mime) or ''


def final_filename(resp, final_url, custom):
    detected = server_filename(resp, final_url)
    suffix = os.path.splitext(detected)[1] or extension_from_type(resp.headers.get('Content-Type', ''))
    chosen = sanitize(custom)
    if chosen:
        return chosen if os.path.splitext(chosen)[1] else chosen + suffix
    if detected:
        return detected if os.path.splitext(detected)[1] else detected + suffix
    return 'file' + suffix


def stream_to_disk(job_id, resp, path, total):
    written = 0
    started = last_time = time.monotonic()
    last_bytes = 0
    jobstore.update(job_id, phase='download', indeterminate=(total == 0))

    with open(path, 'wb') as fh:
        for chunk in resp.iter_content(CHUNK):
            if not chunk:
                continue
            written += len(chunk)
            if written > MAX_BYTES:
                raise FetchError(f'File is larger than the {human_bytes(MAX_BYTES)} limit')
            now = time.monotonic()
            if now - started > MAX_SECONDS:
                raise FetchError('Download took too long and was stopped')
            fh.write(chunk)
            if now - last_time >= 0.5:
                rate = (written - last_bytes) / (now - last_time)
                fields = {'received': human_bytes(written), 'speed': human_bytes(rate) + '/s'}
                if total:
                    fields['percent'] = round(written * 100 / total, 1)
                    fields['eta'] = human_eta((total - written) / rate) if rate > 0 else ''
                jobstore.update(job_id, **fields)
                last_time, last_bytes = now, written

    if written == 0:
        raise FetchError('The server returned an empty file')
    if total and written < total:
        raise FetchError('The connection closed before the file finished')


class DirectEngine(Engine):
    key = 'files'
    route = '/files'
    default_fmt = 'original'
    page = {
        'nav': 'Files',
        'title': 'VidGet — File Downloader',
        'description': 'Download any file from a direct link — documents, archives, installers, images and more.',
        'tagline': 'Fetch any file from a direct link — documents, archives, installers, images and more.',
        'chips': ['PDF', 'ZIP', 'APK', 'ISO', 'Images', 'Docs'],
        'more': 'Any file type',
        'panel_sub': "Paste direct file links — we'll find URLs automatically.",
        'label': 'File URL',
        'placeholder': 'https://example.com/report.pdf or paste multiple links',
        'name_placeholder': 'my-file',
        'formats': False,
        'noun': 'file',
        'footer': 'Files download directly to your device. VidGet does not store your files.',
    }

    def ready(self):
        return True

    def run(self, job_id, url, name, fmt):
        tmp_dir = tempfile.mkdtemp()
        resp = None

        def fail(message):
            jobstore.update(job_id, status='error', error=message)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        try:
            resp, final_url = open_stream(url)
            if resp.status_code >= 400:
                raise FetchError(f'The server returned HTTP {resp.status_code}')
            if looks_like_page(resp):
                raise FetchError('This link opens a web page, not a file. For video links use the Videos page.')

            length = resp.headers.get('Content-Length', '')
            total = int(length) if length.isdigit() else 0
            if total > MAX_BYTES:
                raise FetchError(f'File is larger than the {human_bytes(MAX_BYTES)} limit')

            filename = final_filename(resp, final_url, name)
            path = os.path.join(tmp_dir, filename)
            stream_to_disk(job_id, resp, path, total)

            jobstore.update(
                job_id,
                status='ready',
                percent=100,
                speed='',
                eta='',
                indeterminate=False,
                filepath=path,
                filename=filename,
                mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                phase='done',
            )
        except FetchError as exc:
            fail(str(exc))
        except requests.exceptions.Timeout:
            fail('The server took too long to respond')
        except requests.exceptions.RequestException:
            fail('Could not download — check the URL')
        except Exception as exc:
            fail(str(exc)[:200])
        finally:
            if resp is not None:
                resp.close()
