import time

from django.core.management.base import BaseCommand

from studio.jobs import run_pending


class Command(BaseCommand):
    help = 'Process background jobs (webhook retries, future batch work) with retry and dead-lettering.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run available jobs once and exit')
        parser.add_argument('--interval', type=float, default=2.0, help='Poll interval in seconds')

    def handle(self, *args, **options):
        self.stdout.write('Worker started')
        while True:
            ran = run_pending()
            if ran:
                self.stdout.write(f'Ran {ran} job(s)')
            if options['once']:
                return
            time.sleep(options['interval'])
