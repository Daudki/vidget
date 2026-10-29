import os
import re
import shutil
import subprocess
import tempfile

import jobstore
from .base import Engine

PROGRESS_RE = re.compile(r'\[download\]\s+([\d.]+)%.*?at\s+(\S+)\s+ETA\s+(\S+)')


def tool_available(name):
    try:
        subprocess.run([name, '-version'], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def safe_base(name):
    return re.sub(r'[^\w.\- ]', '_', (name or 'video').strip()) or 'video'


def build_command(tmp_dir, base, fmt, url):
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
        ], 'mp3'
    return common + [
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
        '--merge-output-format', 'mp4',
        url,
    ], 'mp4'


def resolve_output(tmp_dir, base, ext):
    preferred = os.path.join(tmp_dir, f'{base}.{ext}')
    if os.path.isfile(preferred) and os.path.getsize(preferred) > 0:
        return preferred, f'{base}.{ext}'

    best = (None, None)
    best_size = -1
    for entry in os.listdir(tmp_dir):
        if entry.endswith(('.part', '.ytdl', '.temp', '.frag')):
            continue
        path = os.path.join(tmp_dir, entry)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        if size > best_size and entry.endswith(f'.{ext}'):
            best = (path, entry)
            best_size = size
    return best


class VideoEngine(Engine):
    key = 'video'
    route = '/'
    default_fmt = 'mp4'
    page = {
        'nav': 'Videos',
        'title': 'VidGet — Video Downloader',
        'description': 'Download videos and audio from YouTube, TikTok, Instagram, Twitter and 1000+ sites. Free, fast, no account required.',
        'tagline': 'Save videos and audio from your favorite platforms — right in your browser.',
        'chips': ['YouTube', 'TikTok', 'Instagram', 'Twitter/X', 'Facebook'],
        'more': '+1000 sites',
        'panel_sub': "Paste a link or block of text — we'll find URLs automatically.",
        'label': 'Video URL',
        'placeholder': 'https://youtube.com/watch?v=… or paste multiple links',
        'name_placeholder': 'my-video',
        'formats': True,
        'noun': 'video',
        'footer': 'Files download directly to your device. VidGet does not store your media.',
    }

    def ready(self):
        return tool_available('yt-dlp') and tool_available('ffmpeg')

    def details(self):
        return {'yt_dlp': tool_available('yt-dlp'), 'ffmpeg': tool_available('ffmpeg')}

    def run(self, job_id, url, name, fmt):
        tmp_dir = tempfile.mkdtemp()
        base = safe_base(name)
        cmd, ext = build_command(tmp_dir, base, fmt, url)

        def fail(message):
            jobstore.update(job_id, status='error', error=message)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        proc = None
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

                match = PROGRESS_RE.search(line)
                if match:
                    jobstore.update(
                        job_id,
                        percent=float(match.group(1)),
                        speed=match.group(2),
                        eta=match.group(3),
                        phase='download',
                    )
                    continue

                if any(kw in line for kw in ('[Merger]', '[ffmpeg]', 'Merging formats')):
                    current = (jobstore.get(job_id) or {}).get('percent', 0)
                    jobstore.update(
                        job_id,
                        percent=max(current, 99),
                        speed='',
                        eta='',
                        phase='merge',
                    )

            proc.wait(timeout=600)

            if proc.returncode != 0:
                detail = ''
                for entry in reversed(log_tail):
                    if 'ERROR' in entry or 'error' in entry.lower():
                        detail = entry
                        break
                message = detail.replace('ERROR: ', '').strip() if detail else 'Download failed — check the URL'
                if len(message) > 200:
                    message = message[:200] + '...'
                fail(message)
                return

            actual, filename = resolve_output(tmp_dir, base, ext)
            if not actual:
                fail('Merge failed — ffmpeg may be missing on this server.')
                return

            jobstore.update(
                job_id,
                status='ready',
                percent=100,
                filepath=actual,
                filename=filename,
                mime='audio/mpeg' if ext == 'mp3' else 'video/mp4',
                phase='done',
            )

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            fail('Download timed out after 10 minutes. Try a shorter video.')
        except Exception as exc:
            fail(str(exc)[:200])
