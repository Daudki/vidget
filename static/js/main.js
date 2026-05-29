(function () {
  'use strict';

  var queue = [];
  var activeItems = [];
  var selectedFmt = 'mp4';
  var isDownloading = false;
  var deferredInstall = null;

  function $(id) { return document.getElementById(id); }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function toast(msg, type) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast ' + (type || '') + ' show';
    clearTimeout(t._t);
    t._t = setTimeout(function () {
      t.className = 'toast ' + (type || '');
    }, 3200);
  }

  function extractURLs(raw) {
    var clean = raw.replace(/[\u200B\u200C\u200D\uFEFF\u00A0]/g, ' ');
    var matches = clean.match(/https?:\/\/[^\s<>"']+/gi) || [];
    var seen = {};
    return matches.map(function (u) {
      return u.replace(/[)>\]'".,;:!?]+$/, '');
    }).filter(function (u) {
      if (seen[u]) return false;
      seen[u] = true;
      return true;
    });
  }

  function extractURL(raw) {
    var urls = extractURLs(raw);
    return urls.length ? urls[0] : null;
  }

  function domainFromUrl(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch (e) {
      return 'link';
    }
  }

  function faviconLabel(domain) {
    var parts = domain.split('.');
    if (parts.length >= 2) return parts[parts.length - 2].slice(0, 2).toUpperCase();
    return domain.slice(0, 2).toUpperCase();
  }

  function updateDlButton() {
    var btn = $('btnDl');
    var count = $('btnDlCount');
    var text = $('btnDlText');
    var pending = queue.length;
    btn.disabled = pending === 0 || isDownloading;
    if (pending > 0) {
      count.hidden = false;
      count.textContent = pending;
      text.textContent = pending === 1 ? 'Start download' : 'Start downloads (' + pending + ')';
    } else {
      count.hidden = true;
      count.textContent = '';
      text.textContent = 'Start downloads';
    }
    var badge = $('badge');
    badge.textContent = pending;
    badge.setAttribute('data-count', String(pending));
    $('queueSub').textContent = pending
      ? pending + (pending === 1 ? ' item ready' : ' items ready')
      : (activeItems.length ? 'Processing downloads…' : 'Nothing queued yet');
  }

  function renderQueue() {
    var div = $('queueDiv');
    div.querySelectorAll('.item[data-queued]').forEach(function (el) { el.remove(); });
    $('emptyMsg').style.display = (queue.length || activeItems.length) ? 'none' : 'block';
    updateDlButton();

    queue.forEach(function (item) {
      div.appendChild(createItemElement(item, true));
    });

    activeItems.forEach(function (item) {
      if (!item._inDom) {
        div.appendChild(createItemElement(item, false));
        item._inDom = true;
      }
    });
  }

  function createItemElement(item, canRemove) {
    var el = document.createElement('div');
    el.className = 'item' + (item.state ? ' ' + item.state : '');
    el.id = 'qi-' + item.id;
    if (canRemove) el.setAttribute('data-queued', '1');

    var shortUrl = item.url.length > 72 ? item.url.slice(0, 72) + '…' : item.url;
    var fmtLabel = item.fmt === 'mp3' ? 'MP3' : item.fmt === 'best' ? 'Best' : 'MP4';
    var domain = domainFromUrl(item.url);
    var label = faviconLabel(domain);

    el.innerHTML =
      '<div class="item__favicon" aria-hidden="true">' + escapeHtml(label) + '</div>' +
      '<div class="item__body">' +
        '<div class="item__top">' +
          '<div class="item__url" title="' + escapeHtml(item.url) + '">' + escapeHtml(shortUrl) + '</div>' +
          (canRemove ? '<button type="button" class="rm-btn" data-id="' + escapeHtml(item.id) + '" aria-label="Remove from queue">' +
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>' : '') +
        '</div>' +
        '<div class="item__meta">' +
          '<span class="item__tag">' + escapeHtml(fmtLabel) + '</span>' +
          '<span class="item__tag">' + escapeHtml(domain) + '</span>' +
          (item.name ? '<span class="item__tag">' + escapeHtml(item.name) + '</span>' : '<span class="item__tag">Auto name</span>') +
        '</div>' +
        '<div class="prog-wrap" id="pw-' + item.id + '">' +
          '<div class="prog-bar" id="pb-' + item.id + '" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>' +
        '</div>' +
        '<div class="prog-info" id="pi-' + item.id + '">' +
          '<span id="pct-' + item.id + '">0%</span>' +
          '<span id="spd-' + item.id + '"></span>' +
          '<span id="eta-' + item.id + '"></span>' +
        '</div>' +
        '<div class="item-status" id="st-' + item.id + '">Queued</div>' +
      '</div>';

    if (canRemove) {
      el.querySelector('.rm-btn').addEventListener('click', function () {
        if (isDownloading) return;
        queue = queue.filter(function (i) { return i.id !== item.id; });
        renderQueue();
      });
    }

    return el;
  }

  function setItemState(id, state) {
    var el = $('qi-' + id);
    if (!el) return;
    el.className = 'item' + (state ? ' ' + state : '');
    var item = findItem(id);
    if (item) item.state = state;
  }

  function findItem(id) {
    var i;
    for (i = 0; i < queue.length; i++) if (queue[i].id === id) return queue[i];
    for (i = 0; i < activeItems.length; i++) if (activeItems[i].id === id) return activeItems[i];
    return null;
  }

  function setStatus(id, text, cls) {
    var st = $('st-' + id);
    if (!st) return;
    st.textContent = text;
    st.className = 'item-status ' + (cls || '');
  }

  function phaseLabel(phase) {
    if (phase === 'merge') return 'Merging video + audio…';
    if (phase === 'download') return 'Downloading…';
    return 'Downloading…';
  }

  function updateProgress(itemId, d) {
    var pb = $('pb-' + itemId);
    var pw = $('pw-' + itemId);
    var pi = $('pi-' + itemId);
    if (!pb || d.percent === undefined) return;
    pw.style.display = 'block';
    pi.style.display = 'flex';
    pb.style.width = d.percent + '%';
    pb.setAttribute('aria-valuenow', Math.round(d.percent));
    var pct = $('pct-' + itemId);
    var spd = $('spd-' + itemId);
    var eta = $('eta-' + itemId);
    if (pct) pct.textContent = Math.round(d.percent) + '%';
    if (spd) spd.textContent = d.speed || '';
    if (eta) eta.textContent = d.eta ? 'ETA ' + d.eta : '';
  }

  function pollJob(jobId, itemId) {
    return new Promise(function (resolve) {
      var interval = setInterval(function () {
        fetch('/status/' + jobId)
          .then(function (r) { return r.json(); })
          .then(function (d) {
            updateProgress(itemId, d);
            if (d.phase === 'merge') {
              setStatus(itemId, phaseLabel('merge'), 'dl');
            } else if (d.phase === 'download' && d.percent > 0) {
              setStatus(itemId, phaseLabel('download'), 'dl');
            }
            if (d.status === 'ready' || d.status === 'error') {
              clearInterval(interval);
              resolve(d);
            }
          })
          .catch(function () {});
      }, 800);
    });
  }

  function displayFilename(item, serverName) {
    if (serverName) return serverName;
    var ext = item.fmt === 'mp3' ? 'mp3' : 'mp4';
    return (item.name || 'video') + '.' + ext;
  }

  /** Stream file via browser download manager (works for large videos). */
  function startBrowserDownload(jobId) {
    var a = document.createElement('a');
    a.href = '/file/' + encodeURIComponent(jobId);
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function processItem(item) {
    setItemState(item.id, 'is-active');
    setStatus(item.id, 'Starting…', 'dl');

    var jobId;
    try {
      var r = await fetch('/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url, name: item.name, fmt: item.fmt }),
      });
      var d = await r.json();
      if (!r.ok) {
        setItemState(item.id, 'is-error');
        setStatus(item.id, d.error || 'Failed to start', 'err');
        toast(d.error || 'Failed to start', 'err');
        return false;
      }
      jobId = d.job_id;
      if (d.remaining !== undefined) updateLimits(d.remaining);
    } catch (e) {
      setItemState(item.id, 'is-error');
      setStatus(item.id, 'Network error', 'err');
      toast('Network error — check your connection', 'err');
      return false;
    }

    setStatus(item.id, 'Downloading…', 'dl');
    var pw = $('pw-' + item.id);
    var pi = $('pi-' + item.id);
    if (pw) pw.style.display = 'block';
    if (pi) pi.style.display = 'flex';
    var result = await pollJob(jobId, item.id);

    if (result.status === 'error') {
      setItemState(item.id, 'is-error');
      setStatus(item.id, result.error || 'Download failed', 'err');
      toast(result.error || 'Download failed', 'err');
      return false;
    }

    var filename = displayFilename(item, result.filename);
    setStatus(item.id, 'Saving to your device…', 'dl');
    updateProgress(item.id, { percent: 100 });

    try {
      startBrowserDownload(jobId);
      setItemState(item.id, 'is-done');
      setStatus(item.id, 'Download started — check your Downloads folder', 'ok');
      toast('Saving: ' + filename, 'ok');
      return true;
    } catch (e) {
      setItemState(item.id, 'is-error');
      setStatus(item.id, 'Could not start download', 'err');
      toast('Could not save to device — allow downloads in your browser', 'err');
      return false;
    }
  }

  function updateLimits(remaining) {
    var el = $('limitRemaining');
    if (el) el.textContent = remaining + ' / 10';
  }

  async function refreshHealth() {
    var pill = $('statusPill');
    try {
      var r = await fetch('/health');
      var d = await r.json();
      if (d.status === 'ok' && d.yt_dlp && d.ffmpeg) {
        pill.textContent = 'Online';
        pill.className = 'status-pill is-online';
        pill.title = 'yt-dlp and ffmpeg ready';
      } else if (d.yt_dlp && !d.ffmpeg) {
        pill.textContent = 'No ffmpeg';
        pill.className = 'status-pill is-offline';
        pill.title = 'Install ffmpeg for video+audio merge';
      } else {
        pill.textContent = 'Limited';
        pill.className = 'status-pill is-offline';
        pill.title = '';
      }
    } catch (e) {
      pill.textContent = 'Offline';
      pill.className = 'status-pill is-offline';
      pill.title = '';
    }
  }

  async function refreshLimits() {
    try {
      var r = await fetch('/api/limits');
      var d = await r.json();
      updateLimits(d.remaining);
    } catch (e) {
      $('limitRemaining').textContent = '—';
    }
  }

  // Format picker
  document.querySelectorAll('.format-card').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.format-card').forEach(function (b) {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      this.classList.add('active');
      this.setAttribute('aria-pressed', 'true');
      selectedFmt = this.getAttribute('data-fmt');
    });
  });

  var txt = $('txt');
  var charHint = $('charHint');

  $('btnPaste').addEventListener('click', async function () {
    try {
      var clip = await navigator.clipboard.readText();
      if (!clip || !clip.trim()) {
        toast('Clipboard is empty', 'err');
        return;
      }
      txt.value = txt.value ? txt.value.trim() + '\n' + clip.trim() : clip.trim();
      txt.dispatchEvent(new Event('input'));
      txt.focus();
    } catch (e) {
      toast('Allow clipboard access to paste', 'err');
    }
  });

  $('btnClearUrl').addEventListener('click', function () {
    txt.value = '';
    charHint.textContent = '';
    charHint.className = 'input-hint';
    txt.focus();
  });

  txt.addEventListener('input', function () {
    var urls = extractURLs(this.value);
    if (urls.length > 1) {
      charHint.textContent = urls.length + ' URLs detected';
      charHint.className = 'input-hint is-valid';
    } else if (urls.length === 1) {
      charHint.textContent = 'URL detected';
      charHint.className = 'input-hint is-valid';
    } else {
      charHint.textContent = '';
      charHint.className = 'input-hint';
    }
  });

  $('btnAdd').addEventListener('click', function () {
    var raw = txt.value.trim();
    if (!raw) { toast('Paste a video URL first', 'err'); return; }

    var urls = extractURLs(raw);
    if (!urls.length) { toast('No valid URL found', 'err'); return; }

    var name = $('fname').value.trim();
    var added = 0;

    urls.forEach(function (url, idx) {
      var itemName = urls.length > 1 && name ? name + '-' + (idx + 1) : name;
      queue.push({
        id: Date.now().toString() + '-' + idx,
        url: url,
        name: itemName,
        fmt: selectedFmt,
      });
      added++;
    });

    toast(added === 1 ? 'Added to queue' : added + ' items added', 'ok');
    renderQueue();
    txt.value = '';
    $('fname').value = '';
    charHint.textContent = '';
    charHint.className = 'input-hint';
  });

  $('btnClear').addEventListener('click', function () {
    if (isDownloading) { toast('Wait for downloads to finish', 'err'); return; }
    queue = [];
    activeItems = [];
    $('queueDiv').querySelectorAll('.item').forEach(function (el) { el.remove(); });
    renderQueue();
    toast('Queue cleared');
  });

  $('btnDl').addEventListener('click', async function () {
    if (!queue.length || isDownloading) return;

    isDownloading = true;
    this.classList.add('is-loading');
    this.disabled = true;

    var items = queue.slice();
    queue = [];
    activeItems = items.map(function (it) {
      return Object.assign({}, it, { _inDom: false });
    });
    renderQueue();

    var ok = 0;
    for (var i = 0; i < items.length; i++) {
      if (await processItem(items[i])) ok++;
      await refreshLimits();
    }

    isDownloading = false;
    this.classList.remove('is-loading');
    updateDlButton();

    if (ok > 0) {
      toast(ok === items.length ? 'All downloads complete' : ok + ' of ' + items.length + ' succeeded', 'ok');
    }
  });

  // Keyboard shortcut
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (queue.length && !isDownloading) $('btnDl').click();
      else $('btnAdd').click();
    }
  });

  // PWA install
  function showInstallButtons() {
    [$('btnInstall'), $('btnInstallMobile')].forEach(function (btn) {
      if (btn) {
        btn.hidden = false;
        btn.classList.remove('hidden');
      }
    });
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredInstall = e;
    showInstallButtons();
  });

  function installApp() {
    if (!deferredInstall) {
      toast('Install from your browser menu (Add to Home Screen)', '');
      return;
    }
    deferredInstall.prompt();
    deferredInstall.userChoice.then(function () {
      deferredInstall = null;
      [$('btnInstall'), $('btnInstallMobile')].forEach(function (btn) {
        if (btn) { btn.hidden = true; btn.classList.add('hidden'); }
      });
    });
  }

  $('btnInstall').addEventListener('click', installApp);
  $('btnInstallMobile').addEventListener('click', installApp);

  // Service worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
    });
  }

  refreshHealth();
  refreshLimits();
  setInterval(refreshHealth, 60000);
  renderQueue();
})();
