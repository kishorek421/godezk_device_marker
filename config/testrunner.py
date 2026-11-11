import os
import tempfile
import time

from django.conf import settings
from django.db import connections
from django.test.runner import DiscoverRunner


class WinTolerantRunner(DiscoverRunner):
    """DiscoverRunner tolerant of Windows file-locking.

    The file-backed SQLite test DB (needed for real concurrency tests) can
    still be held open briefly by live-server/worker-thread connections and
    -wal/-shm handles when teardown tries to delete it. Give it a unique
    name under the temp dir per run and treat destroy failures as warnings.
    """

    def setup_databases(self, **kwargs):
        test_cfg = settings.DATABASES['default'].setdefault('TEST', {})
        name = test_cfg.get('NAME')
        if name and name != ':memory:':
            base = os.path.join(tempfile.gettempdir(), 'godezk-test')
            test_cfg['NAME'] = f'{base}-{os.getpid()}-{int(time.time() * 1000)}.sqlite3'
        return super().setup_databases(**kwargs)

    def teardown_databases(self, old_config, **kwargs):
        connections.close_all()
        for _ in range(25):
            try:
                return super().teardown_databases(old_config, **kwargs)
            except PermissionError:
                time.sleep(0.2)
        name = settings.DATABASES['default'].get('TEST', {}).get('NAME', '?')
        print(f'\nWarning: could not delete test database {name}; left for OS cleanup.')
