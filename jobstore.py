import threading
import time
import uuid

_jobs = {}
_lock = threading.Lock()

_BLANK = {
    'status': 'running',
    'percent': 0,
    'speed': '',
    'eta': '',
    'received': '',
    'indeterminate': False,
    'phase': 'starting',
    'filepath': None,
    'filename': None,
    'mime': None,
    'error': None,
    'finished': None,
}


def create(engine_key):
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = dict(_BLANK, engine=engine_key)
    return job_id


def update(job_id, **fields):
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)
            if fields.get('status') in ('ready', 'error'):
                job['finished'] = time.time()


def get(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def pop(job_id):
    with _lock:
        return _jobs.pop(job_id, None)


def purge(max_age):
    cutoff = time.time() - max_age
    with _lock:
        stale = [k for k, v in _jobs.items() if v['finished'] and v['finished'] < cutoff]
        return [_jobs.pop(k) for k in stale]
