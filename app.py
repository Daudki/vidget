import subprocess
import os
import re
import threading
import tempfile
import time
import uuid
from collections import defaultdict
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, after_this_request

app = Flask(__name__)

RATE_LIMIT  = 10
RATE_WINDOW = 3600
ip_log  = defaultdict(list)
ip_lock = threading.Lock()

jobs      = {}
jobs_lock = threading.Lock()


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


def run_download(job_id, url, name, fmt):
    tmp_dir = tempfile.mkdtemp()

    if fmt == 'mp3':
        yt_fmt = ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0']
        ext = 'mp3'
    elif fmt == 'best':
        yt_fmt = ['-f', 'bv*+ba/b/best', '--merge-output-format', 'mp4']
        ext = 'mp4'
    else:
        yt_fmt = ['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best', '--merge-output-format', 'mp4']
        ext = 'mp4'

    filename = (name or 'video') + '.' + ext
    outpath = os.path.join(tmp_dir, filename)
    cmd = ['yt-dlp', '--no-playlist', '--newline', '--no-warnings'] + yt_fmt + ['-o', outpath, url]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in proc.stdout:
            m = re.search(r'\[download\]\s+([\d.]+)%.*?at\s+(\S+)\s+ETA\s+(\S+)', line.strip())
            if m:
                with jobs_lock:
                    jobs[job_id].update({
                        'percent': float(m.group(1)),
                        'speed': m.group(2),
                        'eta': m.group(3),
                    })

        proc.wait()

        if proc.returncode != 0:
            with jobs_lock:
                jobs[job_id].update({
                    'status': 'error',
                    'error': 'Download failed — check the URL or try another format',
                })
            return

        actual = outpath
        if not os.path.exists(actual):
            files = os.listdir(tmp_dir)
            if files:
                actual = os.path.join(tmp_dir, files[0])
                filename = files[0]
            else:
                with jobs_lock:
                    jobs[job_id].update({'status': 'error', 'error': 'Output file not found'})
                return

        with jobs_lock:
            jobs[job_id].update({
                'status': 'ready',
                'percent': 100,
                'filepath': actual,
                'filename': filename,
            })

    except Exception as e:
        with jobs_lock:
            jobs[job_id].update({'status': 'error', 'error': str(e)})


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    try:
        subprocess.run(['yt-dlp', '--version'], capture_output=True, check=True, timeout=5)
        yt_ok = True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        yt_ok = False
    return jsonify(status='ok' if yt_ok else 'degraded', yt_dlp=yt_ok)


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
    fmt = (data.get('fmt') or 'mp4').strip()

    if not url:
        return jsonify(error='No URL provided'), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            'status': 'running',
            'percent': 0,
            'speed': '',
            'eta': '',
            'filepath': None,
            'filename': None,
            'error': None,
        }

    threading.Thread(target=run_download, args=(job_id, url, name, fmt), daemon=True).start()
    return jsonify(job_id=job_id, remaining=rate_limit_remaining(ip))


@app.route('/status/<job_id>')
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify(error='Job not found'), 404
    payload = {
        'status': job['status'],
        'percent': job['percent'],
        'speed': job['speed'],
        'eta': job['eta'],
        'error': job['error'],
    }
    if job['status'] == 'ready' and job.get('filename'):
        payload['filename'] = job['filename']
    return jsonify(payload)


@app.route('/file/<job_id>')
def get_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job['status'] != 'ready':
        return jsonify(error='File not ready — try downloading again'), 404

    filepath = job['filepath']
    filename = job['filename']

    if not filepath or not os.path.exists(filepath):
        return jsonify(error='File missing on server'), 404

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'mp4'
    mime = 'audio/mpeg' if ext == 'mp3' else 'video/mp4'
    tmp_dir = os.path.dirname(filepath)

    @after_this_request
    def cleanup(response):
        try:
            if os.path.exists(filepath):
                os.unlink(filepath)
            if os.path.isdir(tmp_dir):
                os.rmdir(tmp_dir)
        except OSError:
            pass
        with jobs_lock:
            jobs.pop(job_id, None)
        return response

    return send_file(
        filepath,
        mimetype=mime,
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
