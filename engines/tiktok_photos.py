import json
import re
import sys
from urllib.parse import urljoin, urlsplit

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 6
MAX_HTML = 5 * 1024 * 1024
DATA_RE = re.compile(r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


class ResolveError(Exception):
    pass


def allowed_host(url):
    host = (urlsplit(url).hostname or '').lower()
    return host == 'tiktok.com' or host.endswith('.tiktok.com')


def fetch_html(url):
    session = requests.Session()
    session.trust_env = False
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if urlsplit(current).scheme not in ('http', 'https') or not allowed_host(current):
            raise ResolveError('Only TikTok links are supported')
        resp = session.get(
            current,
            headers=HEADERS,
            allow_redirects=False,
            timeout=(10, 20),
            stream=True,
        )
        try:
            if resp.status_code in REDIRECT_CODES:
                location = resp.headers.get('Location')
                if not location:
                    raise ResolveError('TikTok sent an invalid redirect')
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise ResolveError(f'TikTok returned HTTP {resp.status_code}')
            chunks = []
            size = 0
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > MAX_HTML:
                    raise ResolveError('Unexpected response from TikTok')
                chunks.append(chunk)
            html = b''.join(chunks).decode(resp.encoding or 'utf-8', errors='replace')
            return current, html
        finally:
            resp.close()
    raise ResolveError('Too many redirects')


def extract(html):
    match = DATA_RE.search(html)
    if not match:
        raise ResolveError('TikTok did not return the post data — the request may have been blocked')
    try:
        data = json.loads(match.group(1))
    except ValueError:
        raise ResolveError('TikTok returned unreadable post data')

    detail = (data.get('__DEFAULT_SCOPE__') or {}).get('webapp.video-detail')
    if not detail:
        raise ResolveError('TikTok did not return the post — it may be private, deleted, or blocked')
    item = (detail.get('itemInfo') or {}).get('itemStruct')
    if not item:
        raise ResolveError(detail.get('statusMsg') or 'Post not found or private')

    image_post = item.get('imagePost')
    if not image_post or not image_post.get('images'):
        raise ResolveError('This post has no photos. For videos use the Videos page.')

    images = []
    for index, entry in enumerate(image_post['images'], start=1):
        urls = (entry.get('imageURL') or {}).get('urlList') or []
        if not urls:
            continue
        preferred = next((u for u in urls if '.jpeg' in u), urls[0])
        images.append({
            'index': index,
            'url': preferred,
            'alternates': [u for u in urls if u != preferred],
            'width': entry.get('imageWidth'),
            'height': entry.get('imageHeight'),
        })
    if not images:
        raise ResolveError('No image links found in this post')

    music = item.get('music') or {}
    return {
        'id': item.get('id'),
        'caption': item.get('desc') or '',
        'author': (item.get('author') or {}).get('uniqueId') or '',
        'images': images,
        'music': {
            'title': music.get('title') or '',
            'author': music.get('authorName') or '',
            'url': music.get('playUrl') or '',
        },
    }


def resolve(url):
    try:
        final_url, html = fetch_html(url)
    except requests.exceptions.Timeout:
        raise ResolveError('TikTok took too long to respond')
    except requests.exceptions.RequestException:
        raise ResolveError('Could not reach TikTok')
    post = extract(html)
    post['url'] = final_url
    return post


def main(argv):
    if len(argv) != 2:
        print('usage: python -m engines.tiktok_photos <tiktok link>', file=sys.stderr)
        return 2
    try:
        post = resolve(argv[1].strip())
    except ResolveError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(post, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
