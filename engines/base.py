class Engine:
    key = ''
    route = ''
    default_fmt = ''
    uses_jobs = True
    page = {}

    def ready(self):
        raise NotImplementedError

    def details(self):
        return {}

    def run(self, job_id, url, name, fmt):
        raise NotImplementedError
