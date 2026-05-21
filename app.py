import subprocess
import os
import re
import threading
import tempfile
import time
import uuid
from collections import defaultdict
from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT = 10
RATE_WINDOW = 3600
ip_log = defaultdict(list)
ip_lock = threading.Lock()

def is_rate_limited(ip):
    now = time.time()
    with ip_lock:
        ip_log[ip] = [t for t in ip_log[ip] if now - t < RATE_WINDOW]
        if len(ip_log[ip]) >= RATE_LIMIT:
            return True
        ip_log[ip].append(now)
    return False

# ── Job store ─────────────────────────────────────────────────────────────────
# jobs[job_id] = { status, percent, speed, eta, filepath, filename, error }
jobs = {}
jobs_lock = threading.Lock()

def run_download(job_id, url, name, fmt):
    tmp_dir = tempfile.mkdtemp()

    if fmt == "mp3":
        yt_fmt = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
        ext = "mp3"
    else:
        yt_fmt = ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
        ext = "mp4"

    filename = (name or "video") + "." + ext
    outpath  = os.path.join(tmp_dir, filename)

    cmd = ["yt-dlp", "--no-playlist", "--newline"] + yt_fmt + ["-o", outpath, url]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            m = re.search(r'\[download\]\s+([\d.]+)%.*?at\s+(\S+)\s+ETA\s+(\S+)', line.strip())
            if m:
                with jobs_lock:
                    jobs[job_id].update({
                        "percent": float(m.group(1)),
                        "speed":   m.group(2),
                        "eta":     m.group(3),
                    })
        proc.wait()

        if proc.returncode != 0:
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"]  = "yt-dlp failed — unsupported or private video"
            return

        # find the actual output file (yt-dlp may adjust extension)
        actual = outpath
        if not os.path.exists(actual):
            files = os.listdir(tmp_dir)
            if files:
                actual = os.path.join(tmp_dir, files[0])
                filename = files[0]
            else:
                with jobs_lock:
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["error"]  = "Output file not found"
                return

        with jobs_lock:
            jobs[job_id].update({
                "status":   "ready",
                "percent":  100,
                "filepath": actual,
                "filename": filename,
            })

    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = str(e)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0"/>
<title>theOutcast VidGet</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg:#080810;--panel:#0e0e1a;--border:#1e1e30;
  --a1:#c8ff00;--a2:#ff2d6b;--a3:#00d4ff;
  --text:#e8e8f0;--muted:#4a4a6a;
  --syne:'Syne',sans-serif;--mono:'DM Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh;overflow-x:hidden;}
body::before{content:'';position:fixed;inset:0;
  background-image:linear-gradient(rgba(200,255,0,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(200,255,0,0.03) 1px,transparent 1px);
  background-size:40px 40px;pointer-events:none;z-index:0;}
body::after{content:'';position:fixed;top:-40%;left:-20%;width:70%;height:70%;
  background:radial-gradient(ellipse,rgba(200,255,0,0.04) 0%,transparent 65%);
  pointer-events:none;z-index:0;animation:pulse 8s ease-in-out infinite;}
@keyframes pulse{0%,100%{transform:scale(1) translate(0,0);opacity:1;}50%{transform:scale(1.1) translate(5%,5%);opacity:0.6;}}
.wrap{position:relative;z-index:1;max-width:600px;margin:0 auto;padding:28px 16px 80px;}
header{margin-bottom:32px;padding-bottom:20px;border-bottom:1px solid var(--border);}
.brand{display:flex;align-items:baseline;gap:10px;margin-bottom:4px;}
.brand-name{font-family:var(--syne);font-weight:800;font-size:clamp(1.8rem,6vw,2.6rem);color:var(--a1);letter-spacing:-1px;line-height:1;}
.brand-tag{font-family:var(--mono);font-size:0.68rem;color:var(--a2);background:rgba(255,45,107,0.1);border:1px solid rgba(255,45,107,0.3);border-radius:4px;padding:2px 8px;letter-spacing:1px;}
.brand-sub{font-size:0.62rem;color:var(--muted);letter-spacing:3px;text-transform:uppercase;margin-bottom:6px;}
.tagline{font-size:0.72rem;color:var(--muted);letter-spacing:2px;text-transform:uppercase;}
.notice{background:rgba(200,255,0,0.04);border:1px solid rgba(200,255,0,0.15);border-radius:8px;padding:10px 14px;font-size:0.72rem;color:var(--muted);margin-bottom:20px;line-height:1.6;}
.notice b{color:var(--a1);}
.section{margin-bottom:20px;}
.section-label{font-size:0.62rem;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:8px;display:flex;align-items:center;gap:8px;}
.section-label::after{content:'';flex:1;height:1px;background:var(--border);}
textarea,input[type=text]{width:100%;background:var(--panel);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:var(--mono);font-size:0.85rem;padding:12px 14px;outline:none;transition:border-color .2s,box-shadow .2s;display:block;}
textarea{height:90px;resize:vertical;line-height:1.5;}
textarea:focus,input:focus{border-color:var(--a1);box-shadow:0 0 0 3px rgba(200,255,0,0.08);}
textarea::placeholder,input::placeholder{color:var(--muted);}
.btn{font-family:var(--syne);font-weight:700;font-size:0.75rem;letter-spacing:1px;text-transform:uppercase;border:none;border-radius:8px;padding:11px 16px;cursor:pointer;transition:all .15s;position:relative;overflow:hidden;}
.btn::after{content:'';position:absolute;inset:0;background:white;opacity:0;transition:opacity .1s;}
.btn:active::after{opacity:0.1;}
.btn-add{background:var(--a1);color:#000;flex:1;}
.btn-add:hover{filter:brightness(1.1);transform:translateY(-1px);}
.btn-clear{background:transparent;color:var(--muted);border:1px solid var(--border);flex:0 0 auto;}
.btn-clear:hover{border-color:var(--a2);color:var(--a2);}
.input-row{display:flex;gap:8px;margin-top:10px;}
.btn-dl{width:100%;background:linear-gradient(135deg,var(--a1) 0%,#80ff00 50%,var(--a1) 100%);background-size:200% 100%;color:#000;font-size:0.8rem;padding:14px;border-radius:10px;letter-spacing:2px;transition:all .3s;font-family:var(--syne);font-weight:800;border:none;cursor:pointer;}
.btn-dl:hover:not(:disabled){background-position:100% 0;transform:translateY(-2px);box-shadow:0 8px 24px rgba(200,255,0,0.25);}
.btn-dl:disabled{background:var(--panel);color:var(--muted);cursor:not-allowed;transform:none;box-shadow:none;border:1px solid var(--border);}
.queue-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
.badge{background:var(--a1);color:#000;font-family:var(--syne);font-weight:800;font-size:0.65rem;padding:2px 9px;border-radius:20px;}
.empty{text-align:center;color:var(--muted);font-size:0.78rem;padding:24px;border:1px dashed var(--border);border-radius:8px;}
.item{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px;animation:itemIn .2s ease;position:relative;overflow:hidden;}
@keyframes itemIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.item-url{font-size:0.75rem;color:var(--a3);word-break:break-all;margin-bottom:4px;padding-right:24px;}
.item-meta{font-size:0.68rem;color:var(--muted);margin-bottom:6px;}
.prog-wrap{height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-bottom:5px;display:none;}
.prog-bar{height:100%;width:0%;background:linear-gradient(90deg,var(--a1),var(--a3));border-radius:2px;transition:width .4s ease;}
.prog-info{display:none;justify-content:space-between;font-size:0.65rem;color:var(--muted);margin-bottom:4px;}
.item-status{font-size:0.72rem;color:var(--muted);}
.item-status.ok{color:var(--a1);}
.item-status.err{color:var(--a2);}
.item-status.dl{color:#ffcc00;}
.rm-btn{position:absolute;top:10px;right:10px;background:none;border:none;color:var(--muted);font-size:0.9rem;cursor:pointer;padding:2px 5px;border-radius:4px;line-height:1;transition:color .15s;}
.rm-btn:hover{color:var(--a2);}
.fmt-row{display:flex;gap:6px;margin-top:8px;}
.fmt-btn{font-family:var(--mono);font-size:0.68rem;padding:5px 10px;border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--muted);cursor:pointer;transition:all .15s;flex:1;text-align:center;}
.fmt-btn.active{border-color:var(--a1);color:var(--a1);background:rgba(200,255,0,0.07);}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(10px);background:var(--panel);border:1px solid var(--a1);color:var(--text);font-family:var(--mono);font-size:0.78rem;padding:9px 18px;border-radius:8px;z-index:99;opacity:0;transition:opacity .25s,transform .25s;pointer-events:none;white-space:nowrap;max-width:90vw;overflow:hidden;text-overflow:ellipsis;}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
#toast.err{border-color:var(--a2);}
footer{text-align:center;color:var(--muted);font-size:0.65rem;letter-spacing:2px;margin-top:40px;text-transform:uppercase;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand-sub">by theOutcast</div>
    <div class="brand">
      <span class="brand-name">VidGet</span>
      <span class="brand-tag">theOutcast</span>
    </div>
    <div class="tagline">Download anything &mdash; no app needed</div>
  </header>

  <div class="notice">
    <b>How it works:</b> paste any link — YouTube, TikTok, Instagram, Twitter/X and 1000+ more.
    The video downloads directly to <b>your device</b>. No account needed.
  </div>

  <div class="section">
    <div class="section-label">Video URL or pasted text</div>
    <textarea id="txt" placeholder="Paste a URL or any text containing one..."></textarea>
    <div style="margin-top:10px">
      <div class="section-label">Output filename <span style="color:var(--muted);font-size:0.6rem;letter-spacing:0">(optional)</span></div>
      <input type="text" id="fname" placeholder="custom-name  (leave blank for auto)"/>
    </div>
    <div style="margin-top:10px">
      <div class="section-label">Format</div>
      <div class="fmt-row">
        <div class="fmt-btn active" data-fmt="mp4" onclick="selectFmt(this)">MP4 Video</div>
        <div class="fmt-btn" data-fmt="mp3" onclick="selectFmt(this)">MP3 Audio</div>
        <div class="fmt-btn" data-fmt="best" onclick="selectFmt(this)">Best Quality</div>
      </div>
    </div>
    <div class="input-row">
      <button class="btn btn-add" id="btnAdd">+ Add to Queue</button>
      <button class="btn btn-clear" id="btnClear">Clear</button>
    </div>
  </div>

  <div class="section">
    <div class="queue-header">
      <div class="section-label" style="margin:0">Queue</div>
      <span class="badge" id="badge">0</span>
    </div>
    <div id="queueDiv">
      <div class="empty" id="emptyMsg">No videos queued yet</div>
    </div>
  </div>

  <button class="btn-dl" id="btnDl" disabled>&#9660;&nbsp; Download All</button>
  <footer style="margin-top:32px">theOutcast VidGet &mdash; powered by yt-dlp</footer>
</div>
<div id="toast"></div>

<script>
(function(){
  var queue = [];
  var selectedFmt = 'mp4';

  function toast(msg, err) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = err ? 'err show' : 'show';
    clearTimeout(t._t);
    t._t = setTimeout(function(){ t.className = err ? 'err' : ''; }, 2800);
  }

  function selectFmt(el) {
    document.querySelectorAll('.fmt-btn').forEach(function(b){ b.classList.remove('active'); });
    el.classList.add('active');
    selectedFmt = el.getAttribute('data-fmt');
  }
  window.selectFmt = selectFmt;

  function extractURL(raw) {
    var clean = raw.replace(/[\u200B\u200C\u200D\uFEFF\u00A0]/g, ' ');
    var m = clean.match(/https?:\/\/[^\s]+/);
    if (!m) return null;
    return m[0].replace(/[)>\]'".,;:!?]+$/, '');
  }

  function renderQueue() {
    var div = document.getElementById('queueDiv');
    document.getElementById('badge').textContent = queue.length;
    document.getElementById('btnDl').disabled = queue.length === 0;
    div.querySelectorAll('.item[data-queued]').forEach(function(el){ el.remove(); });
    document.getElementById('emptyMsg').style.display = queue.length ? 'none' : 'block';
    queue.forEach(function(item) {
      var el = document.createElement('div');
      el.className = 'item'; el.id = 'qi-'+item.id; el.setAttribute('data-queued','1');
      var shortUrl = item.url.length > 55 ? item.url.slice(0,55)+'...' : item.url;
      var fmtLabel = item.fmt === 'mp3' ? 'Audio (MP3)' : item.fmt === 'best' ? 'Best quality' : 'Video (MP4)';
      el.innerHTML =
        '<button class="rm-btn" data-id="'+item.id+'">&#10005;</button>'+
        '<div class="item-url">'+shortUrl+'</div>'+
        '<div class="item-meta">'+(item.name||'Auto filename')+' &middot; '+fmtLabel+'</div>'+
        '<div class="prog-wrap" id="pw-'+item.id+'"><div class="prog-bar" id="pb-'+item.id+'"></div></div>'+
        '<div class="prog-info" id="pi-'+item.id+'"><span id="pct-'+item.id+'">0%</span><span id="spd-'+item.id+'"></span><span id="eta-'+item.id+'"></span></div>'+
        '<div class="item-status" id="st-'+item.id+'">Queued</div>';
      div.appendChild(el);
      el.querySelector('.rm-btn').addEventListener('click', function(){
        var id = this.getAttribute('data-id');
        queue = queue.filter(function(i){ return i.id !== id; });
        renderQueue();
      });
    });
  }

  document.getElementById('btnAdd').addEventListener('click', function(){
    var raw = document.getElementById('txt').value;
    var url = extractURL(raw);
    if (!url) { toast('No URL found in pasted text', true); return; }
    var name = document.getElementById('fname').value.trim();
    var id = Date.now().toString();
    queue.push({ id:id, url:url, name:name, fmt:selectedFmt });
    toast('Added to queue');
    renderQueue();
    document.getElementById('txt').value = '';
    document.getElementById('fname').value = '';
  });

  document.getElementById('btnClear').addEventListener('click', function(){
    queue = []; renderQueue(); toast('Queue cleared');
  });

  // Poll job status and update progress bar
  function pollJob(jobId, resolve) {
    var interval = setInterval(function(){
      fetch('/status/'+jobId)
        .then(function(r){ return r.json(); })
        .then(function(d){
          var pb  = document.getElementById('pb-'+jobId);
          var pw  = document.getElementById('pw-'+jobId);
          var pi  = document.getElementById('pi-'+jobId);
          if (pb && d.percent !== undefined) {
            pw.style.display = 'block'; pi.style.display = 'flex';
            pb.style.width = d.percent + '%';
            var pct = document.getElementById('pct-'+jobId);
            var spd = document.getElementById('spd-'+jobId);
            var eta = document.getElementById('eta-'+jobId);
            if (pct) pct.textContent = Math.round(d.percent)+'%';
            if (spd) spd.textContent = d.speed||'';
            if (eta) eta.textContent = d.eta ? 'ETA '+d.eta : '';
          }
          if (d.status === 'ready' || d.status === 'error') {
            clearInterval(interval);
            resolve(d);
          }
        }).catch(function(){});
    }, 1000);
  }

  document.getElementById('btnDl').addEventListener('click', async function(){
    if (!queue.length) return;
    this.disabled = true;
    var items = queue.slice(); queue = []; renderQueue();

    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var st = document.getElementById('st-'+item.id);
      if (st) { st.textContent = 'Starting...'; st.className = 'item-status dl'; }

      // Step 1: start the job
      var jobId;
      try {
        var r = await fetch('/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url:item.url, name:item.name, fmt:item.fmt, id:item.id })
        });
        var d = await r.json();
        if (!r.ok) {
          if (st) { st.textContent = 'Error: '+(d.error||r.status); st.className = 'item-status err'; }
          toast(d.error||'Failed to start', true); continue;
        }
        jobId = d.job_id;
      } catch(e) {
        if (st) { st.textContent = 'Error: '+e.message; st.className = 'item-status err'; }
        toast('Network error', true); continue;
      }

      if (st) { st.textContent = 'Downloading...'; st.className = 'item-status dl'; }

      // Step 2: poll until ready
      var result = await new Promise(function(resolve){ pollJob(jobId, resolve); });

      if (result.status === 'error') {
        if (st) { st.textContent = 'Error: '+result.error; st.className = 'item-status err'; }
        toast(result.error, true); continue;
      }

      // Step 3: fetch the file
      if (st) { st.textContent = 'Fetching file...'; st.className = 'item-status dl'; }
      try {
        var fr = await fetch('/file/'+jobId);
        if (!fr.ok) {
          if (st) { st.textContent = 'File fetch failed'; st.className = 'item-status err'; }
          toast('File fetch failed', true); continue;
        }
        var blob = await fr.blob();
        var cd = fr.headers.get('Content-Disposition')||'';
        var fnMatch = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';\r\n]+)/i);
        var filename = fnMatch ? decodeURIComponent(fnMatch[1]) : (item.name||'video')+'.mp4';
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a); a.click();
        setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 2000);
        if (st) { st.textContent = 'Done — check your downloads!'; st.className = 'item-status ok'; }
        toast('Downloaded: '+filename);
      } catch(e) {
        if (st) { st.textContent = 'Error: '+e.message; st.className = 'item-status err'; }
        toast('Error: '+e.message, true);
      }
    }
    this.disabled = false;
  });
})();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML


@app.route("/start", methods=["POST"])
def start():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
    if is_rate_limited(ip):
        return jsonify(error="Rate limit: max 10 downloads/hour"), 429

    data   = request.get_json(force=True, silent=True) or {}
    url    = (data.get("url")  or "").strip()
    name   = (data.get("name") or "").strip()
    fmt    = (data.get("fmt")  or "mp4").strip()

    if not url:
        return jsonify(error="No URL provided"), 400

    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {
            "status":   "running",
            "percent":  0,
            "speed":    "",
            "eta":      "",
            "filepath": None,
            "filename": None,
            "error":    None,
        }

    t = threading.Thread(target=run_download, args=(job_id, url, name, fmt), daemon=True)
    t.start()

    return jsonify(job_id=job_id)


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify(error="Job not found"), 404
    return jsonify(
        status=job["status"],
        percent=job["percent"],
        speed=job["speed"],
        eta=job["eta"],
        error=job["error"],
    )


@app.route("/file/<job_id>")
def get_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job["status"] != "ready":
        return jsonify(error="File not ready"), 404

    filepath = job["filepath"]
    filename = job["filename"]

    if not filepath or not os.path.exists(filepath):
        return jsonify(error="File missing on server"), 404

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
    mime = "audio/mpeg" if ext == "mp3" else "video/mp4"

    def generate():
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(1024 * 512)
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

    from flask import stream_with_context
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime,
        "Content-Length": str(os.path.getsize(filepath)),
    }
    return Response(stream_with_context(generate()), headers=headers)


def show_banner():
    try:
        figlet = subprocess.run(["figlet", "-f", "big", "VidGet"], capture_output=True, text=True)
        lolcat = subprocess.run(["lolcat"], input=figlet.stdout, capture_output=True, text=True)
        print(lolcat.stdout, flush=True)
    except FileNotFoundError:
        print("theOutcast VidGet\n")


if __name__ == "__main__":
    show_banner()
    port = int(os.environ.get("PORT", 5000))
    print(f"  http://0.0.0.0:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
