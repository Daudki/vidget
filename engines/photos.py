import re

from werkzeug.http import dump_options_header

from . import direct
from .base import Engine

MAX_ASSET_BYTES = 25 * 1024 * 1024
ASSET_HEADERS = {
    'Referer': 'https://www.tiktok.com/',
    'Accept': 'image/avif,image/webp,image/*,audio/*,*/*;q=0.8',
}
IMAGE_EXT = {
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
    'image/png': 'png',
    'image/heic': 'heic',
    'image/avif': 'avif',
}
AUDIO_EXT = {
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/ogg': 'ogg',
}


def open_asset(url):
    resp, _ = direct.open_stream(url, headers=ASSET_HEADERS)
    if resp.status_code >= 400:
        resp.close()
        raise direct.FetchError(f'The server returned HTTP {resp.status_code}')
    length = resp.headers.get('Content-Length', '')
    if length.isdigit() and int(length) > MAX_ASSET_BYTES:
        resp.close()
        raise direct.FetchError('File is too large')
    return resp


def iter_asset(resp):
    total = 0
    try:
        for chunk in resp.iter_content(direct.CHUNK):
            total += len(chunk)
            if total > MAX_ASSET_BYTES:
                break
            yield chunk
    finally:
        resp.close()


def asset_extension(resp, table, default):
    mime = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()
    return table.get(mime, default)


def slug(text, fallback='tiktok'):
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', text or '').strip('_.')
    return cleaned[:60] or fallback


def attachment_headers(filename):
    return {'Content-Disposition': dump_options_header('attachment', {'filename': filename})}


class PhotosEngine(Engine):
    key = 'photos'
    route = '/photos'
    default_fmt = 'original'
    uses_jobs = False
    page = {
        'nav': 'Photos',
        'title': 'VidGet — Photo Downloader',
        'description': 'Save every image from a TikTok photo post, plus its background track.',
        'tagline': 'Save every image from a TikTok photo post, plus the background track.',
        'chips': ['TikTok'],
        'more': 'Photo posts',
        'panel_sub': "Paste a TikTok photo link — we'll list every image so you can save them.",
        'label': 'Photo post URL',
        'placeholder': 'https://vm.tiktok.com/… or paste multiple links',
        'name_placeholder': '',
        'formats': False,
        'gallery': True,
        'noun': 'photo post',
        'footer': 'Images are fetched on demand and sent straight to your device. VidGet does not store them.',
    }

    def ready(self):
        return True
