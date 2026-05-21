(function () {
  var queue = [];
  var selectedFmt = 'mp4';

  function toast(msg, type) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + (type || '') + ' show';
    clearTimeout(t._t);
    t._t = setTimeout(function () { t.className = 'toast ' + (type || ''); }, 2800);
  }

  function extractURL(raw) {
    var clean = raw.replace(/[\u200B\u200C\u200D\uFEFF\u00A0]/g, ' ');
    var m = clean.match(/https?:\/\/[^\s]+/);
    if (!m) return null;
    return m[0].replace(/[)>\]'".,;:!?]+$/, '');
  }

  function updateDlButton() {
    var btn = document.getElementById('btnDl');
    var count = document.getElementById('btnDlCount');
    var text = document.getElementById('btnDlText');
    btn.disabled = queue.length === 0;
    if (queue.length > 0) {
      count.textContent = queue.length;
      text.textContent = queue.length === 1 ? 'Download' : 'Download All';
    } else {
      count.textContent = '';
      text.textContent = 'Download All';
    }
    document.getElementById('badge').textContent = queue.length;
  }

  function renderQueue() {
    var div = document.getElementById('queueDiv');
    div.querySelectorAll('.item[data-queued]').forEach(function (el) { el.remove(); });
    document.getElementById('emptyMsg').style.display = queue.length ? 'none' : 'block';
    updateDlButton();

    queue.forEach(function (item) {
      var el = document.createElement('div');
      el.className = 'item';
      el.id = 'qi-' + item.id;
      el.setAttribute('data-queued', '1');

      var shortUrl = item.url.length > 60 ? item.url.slice(0, 60) + '…' : item.url;
      var fmtLabel = item.fmt === 'mp3' ? 'MP3' : item.fmt === 'best' ? 'Best' : 'MP4';

      el.innerHTML =
        '<div class="item-accent"></div>' +
        '<button class="rm-btn" data-id="' + item.id + '">✕</button>' +
        '<div class="item-body">' +
          '<div class="item-url">' + shortUrl + '</div>' +
          '<div class="item-meta">' +
            '<span>' + fmtLabel + '</span>' +
            (item.name ? '<span>' + item.name + '</span>' : '<span>auto name</span>') +
          '</div>' +
          '<div class="prog-wrap" id="pw-' + item.id + '">' +
            '<div class="prog-bar" id="pb-' + item.id + '"></div>' +
          '</div>' +
          '<div class="prog-info" id="pi-' + item.id + '">' +
            '<span id="pct-' + item.id + '">0%</span>' +
            '<span id="spd-' + item.id + '"></span>' +
            '<span id="eta-' + item.id + '"></span>' +
          '</div>' +
          '<div class="item-status" id="st-' + item.id + '">Queued</div>' +
        '</div>';

      div.appendChild(el);

      el.querySelector('.rm-btn').addEventListener('click', function () {
        var id = this.getAttribute('data-id');
        queue = queue.filter(function (i) { return i.id !== id; });
        renderQueue();
      });
    });
  }

  // Format picker
  document.querySelectorAll('.fmt-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.fmt-btn').forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');
      selectedFmt = this.getAttribute('data-fmt');
    });
  });

  // Textarea char hint
  var txt = document.getElementById('txt');
  var charHint = document.getElementById('charHint');
  txt.addEventListener('input', function () {
    var url = extractURL(this.value);
    charHint.textContent = url ? '✓ URL detected' : '';
    charHint.style.color = url ? 'var(--a1)' : 'var(--muted)';
  });

  // Add to queue
  document.getElementById('btnAdd').addEventListener('click', function () {
    var raw = document.getElementById('txt').value;
    var url = extractURL(raw);
    if (!url) { toast('No URL found in pasted text', 'err'); return; }
    var name = document.getElementById('fname').value.trim();
    var id = Date.now().toString();
    queue.push({ id: id, url: url, name: name, fmt: selectedFmt });
    toast('Added to queue', 'ok');
    renderQueue();
    document.getElementById('txt').value = '';
    document.getElementById('fname').value = '';
    charHint.textContent = '';
  });

  // Clear queue
  document.getElementById('btnClear').addEventListener('click', function () {
    queue = [];
    renderQueue();
    toast('Queue cleared');
  });

  function setItemState(id, state) {
    var el = document.getElementById('qi-' + id);
    if (!el) return;
    el.className = 'item ' + (state || '');
  }

  function setStatus(id, text, cls) {
    var st = document.getElementById('st-' + id);
    if (!st) return;
    st.textContent = text;
    st.className = 'item-status ' + (cls || '');
  }

  function pollJob(jobId, resolve) {
    var interval = setInterval(function () {
      fetch('/status/' + jobId)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var pb  = document.getElementById('pb-' + jobId);
          var pw  = document.getElementById('pw-' + jobId);
          var pi  = document.getElementById('pi-' + jobId);
          if (pb && d.percent !== undefined) {
            pw.style.display = 'block';
            pi.style.display = 'flex';
            pb.style.width = d.percent + '%';
            var pct = document.getElementById('pct-' + jobId);
            var spd = document.getElementById('spd-' + jobId);
            var eta = document.getElementById('eta-' + jobId);
            if (pct) pct.textContent = Math.round(d.percent) + '%';
            if (spd) spd.textContent = d.speed || '';
            if (eta) eta.textContent = d.eta ? 'ETA ' + d.eta : '';
          }
          if (d.status === 'ready' || d.status === 'error') {
            clearInterval(interval);
            resolve(d);
          }
        }).catch(function () {});
    }, 1000);
  }

  document.getElementById('btnDl').addEventListener('click', async function () {
    if (!queue.length) return;
    this.disabled = true;
    var items = queue.slice();
    queue = [];
    renderQueue();

    for (var i = 0; i < items.length; i++) {
      var item = items[i];

      setItemState(item.id, 'is-active');
      setStatus(item.id, 'Starting…', 'dl');

      var jobId;
      try {
        var r = await fetch('/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: item.url, name: item.name, fmt: item.fmt })
        });
        var d = await r.json();
        if (!r.ok) {
          setItemState(item.id, 'is-error');
          setStatus(item.id, d.error || 'Failed to start', 'err');
          toast(d.error || 'Failed', 'err');
          continue;
        }
        jobId = d.job_id;
      } catch (e) {
        setItemState(item.id, 'is-error');
        setStatus(item.id, 'Network error', 'err');
        toast('Network error', 'err');
        continue;
      }

      setStatus(item.id, 'Downloading…', 'dl');
      var result = await new Promise(function (resolve) { pollJob(jobId, resolve); });

      if (result.status === 'error') {
        setItemState(item.id, 'is-error');
        setStatus(item.id, result.error, 'err');
        toast(result.error, 'err');
        continue;
      }

      setStatus(item.id, 'Fetching file…', 'dl');
      try {
        var fr = await fetch('/file/' + jobId);
        if (!fr.ok) {
          setItemState(item.id, 'is-error');
          setStatus(item.id, 'File fetch failed', 'err');
          toast('File fetch failed', 'err');
          continue;
        }
        var blob = await fr.blob();
        var cd = fr.headers.get('Content-Disposition') || '';
        var fnMatch = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';\r\n]+)/i);
        var filename = fnMatch ? decodeURIComponent(fnMatch[1]) : (item.name || 'video') + '.mp4';

        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 2000);

        var pb = document.getElementById('pb-' + item.id);
        if (pb) pb.style.width = '100%';
        setItemState(item.id, 'is-done');
        setStatus(item.id, 'Done — saved to downloads', 'ok');
        toast(filename + ' downloaded', 'ok');
      } catch (e) {
        setItemState(item.id, 'is-error');
        setStatus(item.id, e.message, 'err');
        toast(e.message, 'err');
      }
    }

    updateDlButton();
  });
})();