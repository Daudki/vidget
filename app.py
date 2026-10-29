import os
import shutil
import threading
import time
from collections import defaultdict

from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, after_this_request

import jobstore
from engines import ENGINES, DEFAULT_ENGINE

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
