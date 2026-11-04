import json
import re
import sys
from urllib.parse import urljoin, urlsplit

import requests

import config

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 6
MAX_HTML = 5 * 1024 * 1024
SCRIPT_RE = re.compile(r'<script([^>]*)>(.*?)</script>', re.S)
ID_RE = re.compile(r'\bid="([^"]+)"')
POST_ID_RE = re.compile(r'/(?:video|photo)/(\d{8,})|/v/(\d{8,})')
EMBED_URL = 'https://www.tiktok.com/embed/v2/{}'
MAX_DIAG = 300


class ResolveError(Exception):
    def __init__(self, message, diag='', terminal=False):
        super().__init__(message)
        self.message = message
        self.diag = diag
        self.terminal = terminal

    def __str__(self):
        return f'{self.message} [{self.diag[:MAX_DIAG]}]' if self.diag else self.message


def allowed_host(url):
    host = (urlsplit(url).hostname or '').lower()
    return host == 'tiktok.com' or host.endswith('.tiktok.com')


def new_session():
    session = requests.Session()
    session.trust_env = False
    return session


def fetch_html(url, session=None):
    session = session or new_session()
    proxies = config.requests_proxies('photos')
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
            proxies=proxies,
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


def json_blocks(html):
    blocks = []
    for attrs, body in SCRIPT_RE.findall(html):
        body = body.strip()
        if not body.startswith(('{', '[')):
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        match = ID_RE.search(attrs)
        blocks.append((match.group(1) if match else 'json', data))
    return blocks


def walk(data):
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def locate(blocks):
    for _, data in blocks:
        for node in walk(data):
            post = node.get('imagePost')
            if isinstance(post, dict) and post.get('images'):
                return 'detail', node
            info = node.get('imagePostInfo')
            if isinstance(info, dict) and info.get('displayImages'):
                return 'embed', node
    return None, None


def find_urls(node):
    if isinstance(node, str):
        return [node] if node.startswith('http') else []
    if isinstance(node, list):
        flat = [x for x in node if isinstance(x, str) and x.startswith('http')]
        if flat:
            return flat
        for item in node:
            found = find_urls(item)
            if found:
                return found
        return []
    if isinstance(node, dict):
        for key in ('imageURL', 'displayImage', 'urlList', 'url_list', 'UrlList'):
            if key in node:
                found = find_urls(node[key])
                if found:
                    return found
        for value in node.values():
            found = find_urls(value)
            if found:
                return found
    return []


def first_text(*values):
    for value in values:
        if isinstance(value, list):
            value = value[0] if value else ''
        if isinstance(value, str) and value:
            return value
    return ''


def build_images(entries):
    images = []
    for index, entry in enumerate(entries, start=1):
        urls = find_urls(entry)
        if not urls:
            continue
        preferred = next((u for u in urls if '.jpeg' in u), urls[0])
        images.append({
            'index': index,
            'url': preferred,
            'alternates': [u for u in urls if u != preferred],
            'width': entry.get('imageWidth') or entry.get('width'),
            'height': entry.get('imageHeight') or entry.get('height'),
        })
    return images


def build_post(kind, node):
    if kind == 'detail':
        entries = node['imagePost']['images']
        music = node.get('music') or {}
        info = node
        author = (node.get('author') or {}).get('uniqueId')
        caption = node.get('desc')
        post_id = node.get('id')
        music_title = music.get('title')
        music_author = music.get('authorName')
        music_url = music.get('playUrl')
    else:
        entries = node['imagePostInfo']['displayImages']
        info = node.get('itemInfos') or {}
        music = node.get('musicInfos') or {}
        author = (node.get('authorInfos') or {}).get('uniqueId')
        caption = info.get('text') or node.get('desc')
        post_id = info.get('id') or node.get('id')
        music_title = music.get('musicName') or music.get('title')
        music_author = music.get('authorName')
        music_url = music.get('playUrl')

    images = build_images(entries)
    if not images:
        return None
    return {
        'id': first_text(post_id),
        'caption': first_text(caption),
        'author': first_text(author),
        'images': images,
        'music': {
            'title': first_text(music_title),
            'author': first_text(music_author),
            'url': first_text(music_url),
        },
    }


def describe(blocks):
    parts = []
    for block_id, data in blocks[:4]:
        scope = data.get('__DEFAULT_SCOPE__') if isinstance(data, dict) else None
        source = scope if isinstance(scope, dict) else data
        keys = list(source)[:8] if isinstance(source, dict) else []
        parts.append(f"{block_id}: {', '.join(str(k) for k in keys)}")
    return '; '.join(parts) or 'no embedded data'


def extract(html):
    blocks = json_blocks(html)
    if not blocks:
        raise ResolveError(
            'TikTok did not return the post data — the request may have been blocked',
            diag=describe(blocks),
        )

    kind, node = locate(blocks)
    if node:
        post = build_post(kind, node)
        if post:
            return post
        raise ResolveError('No image links found in this post', diag=describe(blocks))

    for _, data in blocks:
        scope = data.get('__DEFAULT_SCOPE__') if isinstance(data, dict) else None
        detail = (scope or {}).get('webapp.video-detail')
        if not detail:
            continue
        item = (detail.get('itemInfo') or {}).get('itemStruct')
        if item:
            raise ResolveError('This post has no photos. For videos use the Videos page.', terminal=True)
        status = detail.get('statusMsg')
        if status:
            raise ResolveError(status, terminal=True)

    raise ResolveError(
        'TikTok did not return the post — it may be private, deleted, or blocked',
        diag=describe(blocks),
    )


def post_id_from(url):
    match = POST_ID_RE.search(urlsplit(url).path)
    return (match.group(1) or match.group(2)) if match else ''


def resolve(url):
    session = new_session()
    try:
        final_url, html = fetch_html(url, session)
    except requests.exceptions.ProxyError:
        raise ResolveError('The proxy refused the connection or is unreachable')
    except requests.exceptions.Timeout:
        raise ResolveError('TikTok took too long to respond')
    except requests.exceptions.RequestException:
        raise ResolveError('Could not reach TikTok')

    try:
        post = extract(html)
    except ResolveError as first:
        post_id = post_id_from(final_url)
        if first.terminal or not post_id:
            raise
        try:
            _, embed_html = fetch_html(EMBED_URL.format(post_id), session)
            post = extract(embed_html)
        except ResolveError as second:
            raise ResolveError(
                first.message,
                diag=f'page: {first.diag}; embed: {second.diag or second.message}',
            )
        except requests.exceptions.RequestException:
            raise ResolveError(first.message, diag=f'page: {first.diag}; embed: unreachable')

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
