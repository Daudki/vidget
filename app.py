import subprocess
import os
import re
import threading
import tempfile
import time
import uuid
from collections import defaultdict
from flask import Flask, request, jsonify, Response, render_template, stream_with_context

app = Flask(__name__)

RATE_LIMIT  = 10
RATE_WINDOW = 3600
ip_log  = defaultdict(list)
ip_lock = threading.Lock()

jobs      = {}
jobs_lock = threading.Lock()


def is_rate_limited(ip):
    now = time.time()
    with ip_lock:
        ip_log[ip] = [t for t in ip_log[ip] if now - t < RATE_WINDOW]
        if len(ip_log[ip]) >= RATE_LIMIT:
            return True
        ip_log[ip].append(now)
    return False


def run_download(job_id, url, name, fmt):
    tmp_dir  = tempfile.mkdtemp()

    if fmt == 'mp3':
        yt_fmt = ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0']
        ext    = 'mp3'
    else:
        yt_fmt = ['-f', 'bestvideo+bestaudio/best', '--merge-output-format', 'mp4']
        ext    = 'mp4'

    filename = (name or 'video') + '.' + ext
    outpath  = os.path.join(tmp_dir, filename)
    cmd      = ['yt-dlp', '--no-playlist', '--newline'] + yt_fmt + ['-o', outpath, url]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in proc.stdout:
            m = re.search(r'\[download\]\s+([\d.]+)%.*?at\s+(\S+)\s+ETA\s+(\S+)', line.strip())
            if m:
                with jobs_lock:
                    jobs[job_id].update({
                        'percent': float(m.group(1)),
                        'speed':   m.group(2),
                        'eta':     m.group(3),
                    })

        proc.wait()

        if proc.returncode != 0:
            with jobs_lock:
                jobs[job_id].update({'status': 'error', 'error': 'yt-dlp failed — unsupported or private video'})
            return

        actual = outpath
        if not os.path.exists(actual):
            files = os.listdir(tmp_dir)
            if files:
                actual   = os.path.join(tmp_dir, files[0])
                filename = files[0]
            else:
                with jobs_lock:
                    jobs[job_id].update({'status': 'error', 'error': 'Output file not found'})
                return

        with jobs_lock:
            jobs[job_id].update({'status': 'ready', 'percent': 100, 'filepath': actual, 'filename': filename})

    except Exception as e:
        with jobs_lock:
            jobs[job_id].update({'status': 'error', 'error': str(e)})


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start', methods=['POST'])
def start():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    if is_rate_limited(ip):
        return jsonify(error='Rate limit: max 10 downloads/hour'), 429

    data = request.get_json(force=True, silent=True) or {}
    url  = (data.get('url')  or '').strip()
    name = (data.get('name') or '').strip()
    fmt  = (data.get('fmt')  or 'mp4').strip()

    if not url:
        return jsonify(error='No URL provided'), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {'status': 'running', 'percent': 0, 'speed': '', 'eta': '', 'filepath': None, 'filename': None, 'error': None}

    threading.Thread(target=run_download, args=(job_id, url, name, fmt), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route('/status/<job_id>')
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify(error='Job not found'), 404
    return jsonify(status=job['status'], percent=job['percent'], speed=job['speed'], eta=job['eta'], error=job['error'])


@app.route('/file/<job_id>')
def get_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job['status'] != 'ready':
        return jsonify(error='File not ready'), 404

    filepath = job['filepath']
    filename = job['filename']

    if not filepath or not os.path.exists(filepath):
        return jsonify(error='File missing on server'), 404

    ext  = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'mp4'
    mime = 'audio/mpeg' if ext == 'mp3' else 'video/mp4'

    def generate():
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(524288)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(filepath)
                os.rmdir(os.path.dirname(filepath))
            except Exception:
                pass
            with jobs_lock:
                jobs.pop(job_id, None)

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type':        mime,
        'Content-Length':      str(os.path.getsize(filepath)),
    }
    return Response(stream_with_context(generate()), headers=headers)


def show_banner():
    try:
        figlet = subprocess.run(['figlet', '-f', 'big', 'VidGet'], capture_output=True, text=True)
        lolcat = subprocess.run(['lolcat'], input=figlet.stdout, capture_output=True, text=True)
        print(lolcat.stdout, flush=True)
    except FileNotFoundError:
        print('theOutcast VidGet\n')


if __name__ == '__main__':
    show_banner()
    port = int(os.environ.get('PORT', 5000))
    print(f'  http://0.0.0.0:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
