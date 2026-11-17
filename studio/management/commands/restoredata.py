import os
import shutil
import sqlite3
import tarfile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Restore a backup produced by backupdata (SQL dump + media). Stop the server first.'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the backup .tar.gz')
        parser.add_argument('--db', help='Target db path (defaults to the configured database)')

    def handle(self, *args, **options):
        path = options['file']
        if connection.vendor != 'sqlite':
            raise CommandError('restoredata supports SQLite deployments; restore Postgres with pg_restore')
        if not tarfile.is_tarfile(path):
            raise CommandError(f'{path} is not a backup archive')
        with tarfile.open(path) as tar:
            member = next((m for m in tar.getmembers() if m.name == 'db.sqlite3.sql'), None)
            if not member:
                raise CommandError('Archive does not contain db.sqlite3.sql')
            sql = tar.extractfile(member).read().decode()
            live = options['db'] is None
            db_path = options['db'] or connection.settings_dict['NAME']
            fresh_path = db_path + '.restore'
            if os.path.exists(fresh_path):
                os.remove(fresh_path)
            fresh = sqlite3.connect(fresh_path)
            fresh.executescript(sql)
            fresh.close()
            restored = 0
            for m in tar.getmembers():
                if not (m.name.startswith('media/') and m.isfile()):
                    continue
                rel = m.name[len('media/'):]
                if '..' in rel.split('/'):
                    continue
                dest = os.path.join(settings.MEDIA_ROOT, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with tar.extractfile(m) as src, open(dest, 'wb') as out:
                    shutil.copyfileobj(src, out)
                restored += 1
        if live:
            connection.close()
        os.replace(fresh_path, db_path)
        self.stdout.write(self.style.SUCCESS(f'Restored database and {restored} media file(s) from {path}'))
