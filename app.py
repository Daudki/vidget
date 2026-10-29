import os
import shutil
import tempfile
import threading
import time
import zipfile
from collections import defaultdict

import requests
from flask import Flask, Response, request, jsonify, render_template, send_from_directory, send_file, after_this_request

import jobstore
import photostore
from engines import ENGINES, DEFAULT_ENGINE
from engines import photos as photos_engine
from engines import tiktok_photos
from engines.direct import FetchError

app = Flask(__name__)

RATE_LIMIT  = 10
RATE_WINDOW = 3600
ip_log  = defaultdict(list)
ip_lock = threading.Lock()


def get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()


def is_rate_limited(ip):
    now = time.time()
    with ip_lock:
        ip_log[ip] = [t for t in ip_log[ip] if now - t < RATE_WINDOW]
        if len(ip_log[ip]) >= RATE_LIMIT:
            return True
        ip_log[ip].append(now)
    return False


def rate_limit_remaining(ip):
    now = time.time()
    with ip_lock:
        recent = [t for t in ip_log[ip] if now - t < RATE_WINDOW]
        return max(0, RATE_LIMIT - len(recent))


def make_page_view(engine):
    def view():
        return render_template(
            'page.html',
            engine=engine,
            page=engine.page,
            engines=list(ENGINES.values()),
        )
    return view


for _engine in ENGINES.values():
    app.add_url_rule(_engine.route, endpoint='page_' + _engine.key, view_func=make_page_view(_engine))


@app.route('/health')
def health():
    readiness = {key: engine.ready() for key, engine in ENGINES.items()}
    details = {}
    for engine in ENGINES.values():
        details.update(engine.details())
    return jsonify(
        status='ok' if all(readiness.values()) else 'degraded',
        engines=readiness,
        **details,
    )


@app.route('/api/limits')
def limits():
    ip = get_client_ip()
    return jsonify(limit=RATE_LIMIT, remaining=rate_limit_remaining(ip), window_hours=1)


@app.route('/start', methods=['POST'])
def start():
    ip = get_client_ip()
    if is_rate_limited(ip):
        return jsonify(error='Rate limit reached — max 10 downloads per hour'), 429

    data = request.get_json(force=True, silent=True) or {}
    url = (data.get('url') or '').strip()
    name = (data.get('name') or '').strip()
    engine = ENGINES.get((data.get('engine') or DEFAULT_ENGINE).strip())

    if not engine:
        return jsonify(error='Unknown download engine'), 400
    if not url:
        return jsonify(error='No URL provided'), 400
    if not engine.uses_jobs:
        return jsonify(error='This engine does not use download jobs'), 400

    fmt = (data.get('fmt') or engine.default_fmt).strip()
    job_id = jobstore.create(engine.key)
    threading.Thread(target=engine.run, args=(job_id, url, name, fmt), daemon=True).start()
    return jsonify(job_id=job_id, remaining=rate_limit_remaining(ip))


@app.route('/status/<job_id>')
def status(job_id):
    job = jobstore.get(job_id)
    if not job:
        return jsonify(error='Job not found'), 404
    payload = {
        'status': job['status'],
        'percent': job['percent'],
        'speed': job['speed'],
        'eta': job['eta'],
        'received': job['received'],
        'indeterminate': job['indeterminate'],
        'phase': job['phase'],
        'error': job['error'],
    }
    if job['status'] == 'ready' and job.get('filename'):
        payload['filename'] = job['filename']
    return jsonify(payload)


@app.route('/file/<job_id>')
def get_file(job_id):
    job = jobstore.get(job_id)
    if not job or job['status'] != 'ready':
        return jsonify(error='File not ready'), 404

    filepath = job['filepath']
    filename = job['filename']

    if not filepath or not os.path.exists(filepath):
        return jsonify(error='File missing on server'), 404

    tmp_dir = os.path.dirname(filepath)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        jobstore.pop(job_id)
        return response

    return send_file(
        filepath,
        mimetype=job.get('mime') or 'application/octet-stream',
        as_attachment=True,
        download_name=filename,
        conditional=True,
        max_age=0,
    )


@app.route('/photos/resolve', methods=['POST'])
def photos_resolve():
    ip = get_client_ip()
    if is_rate_limited(ip):
        return jsonify(error='Rate limit reached — max 10 downloads per hour'), 429

    data = request.get_json(force=True, silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify(error='No URL provided'), 400

    try:
        post = tiktok_photos.resolve(url)
    except tiktok_photos.ResolveError as exc:
        return jsonify(error=str(exc), remaining=rate_limit_remaining(ip)), 422

    token = photostore.put(post)
    music = post['music']
    return jsonify(
        token=token,
        author=post['author'],
        caption=post['caption'][:300],
        count=len(post['images']),
        images=[
            {'index': i['index'], 'width': i['width'], 'height': i['height']}
            for i in post['images']
        ],
        music={'title': music['title'], 'author': music['author']} if music['url'] else None,
        remaining=rate_limit_remaining(ip),
    )


def photo_basename(post):
    return 'tiktok_{}_{}'.format(photos_engine.slug(post['author']), photos_engine.slug(post['id'] or '', 'post'))


def stream_asset(url, filename_for, table, default_ext, as_attachment):
    try:
        resp = photos_engine.open_asset(url)
    except (FetchError, requests.exceptions.RequestException):
        return jsonify(error='Could not fetch the file from TikTok'), 502
    ext = photos_engine.asset_extension(resp, table, default_ext)
    mime = resp.headers.get('Content-Type', '').split(';')[0].strip() or 'application/octet-stream'
    headers = {'Cache-Control': 'private, max-age=1500'}
    if as_attachment:
        headers.update(photos_engine.attachment_headers(filename_for(ext)))
    length = resp.headers.get('Content-Length', '')
    if length.isdigit():
        headers['Content-Length'] = length
    return Response(photos_engine.iter_asset(resp), mimetype=mime, headers=headers)


@app.route('/photos/img/<token>/<int:index>')
def photos_image(token, index):
    post = photostore.get(token)
    image = next((i for i in (post or {}).get('images', []) if i['index'] == index), None)
    if not image:
        return jsonify(error='Photo not found or expired'), 404
    base = photo_basename(post)
    return stream_asset(
        image['url'],
        lambda ext: f'{base}_{index}.{ext}',
        photos_engine.IMAGE_EXT,
        'jpg',
        request.args.get('dl') == '1',
    )


@app.route('/photos/music/<token>')
def photos_music(token):
    post = photostore.get(token)
    if not post or not post['music']['url']:
        return jsonify(error='Music not found or expired'), 404
    base = photo_basename(post)
    return stream_asset(
        post['music']['url'],
        lambda ext: f'{base}_music.{ext}',
        photos_engine.AUDIO_EXT,
        'mp3',
        True,
    )


@app.route('/photos/zip/<token>')
def photos_zip(token):
    post = photostore.get(token)
    if not post:
        return jsonify(error='Photos not found or expired'), 404

    base = photo_basename(post)
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, base + '.zip')
    added = 0
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
        for image in post['images']:
            try:
                resp = photos_engine.open_asset(image['url'])
            except (FetchError, requests.exceptions.RequestException):
                continue
            ext = photos_engine.asset_extension(resp, photos_engine.IMAGE_EXT, 'jpg')
            with archive.open(f"{base}_{image['index']}.{ext}", 'w') as dest:
                for chunk in photos_engine.iter_asset(resp):
                    dest.write(chunk)
            added += 1

    if not added:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify(error='Could not fetch the photos from TikTok'), 502

    @after_this_request
    def cleanup(response):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return response

    return send_file(path, mimetype='application/zip', as_attachment=True, download_name=base + '.zip', max_age=0)


@app.route('/manifest.webmanifest')
def manifest():
    return send_from_directory('static', 'manifest.webmanifest', mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'VidGet running at http://0.0.0.0:{port}')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
