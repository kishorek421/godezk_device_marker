import io
import os
import tarfile
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Back up the database and uploaded media into a timestamped .tar.gz archive.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default='backups', help='Directory for the archive')

    def handle(self, *args, **options):
        if connection.vendor != 'sqlite':
            raise CommandError('backupdata supports SQLite deployments; use pg_dump plus object-storage sync for Postgres')
        out_dir = options['out']
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f'backup-{time.strftime("%Y%m%d-%H%M%S")}.tar.gz')
        with tarfile.open(path, 'w:gz') as tar:
            dump = b'\n'.join(line.encode() for line in connection.connection.iterdump())
            info = tarfile.TarInfo('db.sqlite3.sql')
            info.size = len(dump)
            tar.addfile(info, io.BytesIO(dump))
            media = settings.MEDIA_ROOT
            if os.path.isdir(media):
                tar.add(media, arcname='media')
        self.stdout.write(self.style.SUCCESS(f'Backup written to {path}'))
