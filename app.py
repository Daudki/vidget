import subprocess
import os
import re
import shutil
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


def tool_available(name):
    try:
        subprocess.run([name, '-version'], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def build_ytdlp_command(tmp_dir, base_name, fmt, url):
    base = re.sub(r'[^\w.\- ]', '_', (base_name or 'video').strip()) or 'video'
    out_tpl = os.path.join(tmp_dir, base + '.%(ext)s')

    common = [
        'yt-dlp',
        '--no-playlist',
        '--newline',
        '--no-warnings',
        '--no-keep-video',
        '--socket-timeout', '300',
        '--retries', '20',
        '--fragment-retries', '20',
        '--no-check-certificates',
        '--force-ipv4',
        '-o', out_tpl,
    ]

    if fmt == 'mp3':
        return common + [
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            url,
        ], base, 'mp3'

    # SINGLE LINE THAT ALWAYS GETS AUDIO
    return common + [
        '-f', 'mp4',
        url,
    ], base, 'mp4'

def resolve_output_file(tmp_dir, base_name, ext):
    base = re.sub(r'[^\w.\- ]', '_', (base_name or 'video').strip()) or 'video'
    preferred = os.path.join(tmp_dir, f'{base}.{ext}')
    if os.path.isfile(preferred) and os.path.getsize(preferred) > 0:
        return preferred, f'{base}.{ext}'

    best = (None, None)
    best_size = -1
    for name in os.listdir(tmp_dir):
        if name.endswith(('.part', '.ytdl', '.temp', '.frag')):
            continue
        path = os.path.join(tmp_dir, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        if size > best_size and name.endswith(f'.{ext}'):
            best = (path, name)
            best_size = size

    return best


def run_download(job_id, url, name, fmt):
    tmp_dir = tempfile.mkdtemp()
    cmd, base_name, ext = build_ytdlp_command(tmp_dir, name, fmt, url)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        log_tail = []
        for line in proc.stdout:
            line = line.strip()
            if line:
                log_tail.append(line)
                if len(log_tail) > 40:
                    log_tail.pop(0)

            m = re.search(r'\[download\]\s+([\d.]+)%.*?at\s+(\S+)\s+ETA\s+(\S+)', line)
            if m:
                with jobs_lock:
                    jobs[job_id].update({
                        'percent': float(m.group(1)),
                        'speed': m.group(2),
                        'eta': m.group(3),
                        'phase': 'download',
                    })
                continue

            if any(kw in line for kw in ('[Merger]', '[ffmpeg]', 'Merging formats')):
                with jobs_lock:
                    jobs[job_id].update({
                        'percent': max(jobs[job_id].get('percent', 0), 99),
                        'speed': '',
                        'eta': '',
                        'phase': 'merge',
                    })

        proc.wait(timeout=600)

        if proc.returncode != 0:
            detail = ''
            for ln in reversed(log_tail):
                if 'ERROR' in ln or 'error' in ln.lower():
                    detail = ln
                    break
            msg = detail.replace('ERROR: ', '').strip() if detail else 'Download failed — check the URL'
            if len(msg) > 200:
                msg = msg[:200] + '...'
            with jobs_lock:
                jobs[job_id].update({'status': 'error', 'error': msg})
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        actual, filename = resolve_output_file(tmp_dir, base_name, ext)
        if not actual:
            with jobs_lock:
                jobs[job_id].update({
                    'status': 'error',
                    'error': 'Merge failed — ffmpeg may be missing on this server.',
                })
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        with jobs_lock:
            jobs[job_id].update({
                'status': 'ready',
                'percent': 100,
                'filepath': actual,
                'filename': filename,
                'phase': 'done',
            })

    except subprocess.TimeoutExpired:
        proc.kill()
        with jobs_lock:
            jobs[job_id].update({
                'status': 'error',
                'error': 'Download timed out after 10 minutes. Try a shorter video.',
            })
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        with jobs_lock:
            jobs[job_id].update({'status': 'error', 'error': str(e)[:200]})
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    yt_ok = tool_available('yt-dlp')
    ffmpeg_ok = tool_available('ffmpeg')
    ready = yt_ok and ffmpeg_ok
    return jsonify(
        status='ok' if ready else 'degraded',
        yt_dlp=yt_ok,
        ffmpeg=ffmpeg_ok,
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
            'phase': 'starting',
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
        'phase': job.get('phase', ''),
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
        return jsonify(error='File not ready'), 404

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
            shutil.rmtree(tmp_dir, ignore_errors=True)
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