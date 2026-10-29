import secrets
import threading
import time

TTL_SECONDS = 1800
MAX_ENTRIES = 500

_posts = {}
_lock = threading.Lock()


def put(post):
    token = secrets.token_urlsafe(12)
    now = time.time()
    with _lock:
        for key in [k for k, v in _posts.items() if v['expires'] < now]:
            del _posts[key]
        if len(_posts) >= MAX_ENTRIES:
            oldest = min(_posts, key=lambda k: _posts[k]['expires'])
            del _posts[oldest]
        _posts[token] = dict(post, expires=now + TTL_SECONDS)
    return token


def get(token):
    with _lock:
        post = _posts.get(token)
        if post and post['expires'] >= time.time():
            return dict(post)
    return None
