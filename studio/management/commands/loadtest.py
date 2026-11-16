import concurrent.futures
import statistics
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from studio.models import Label, Membership, Project, Task, Workspace


class Command(BaseCommand):
    help = 'Load-check the hot paths: bulk-create tasks, then measure queue-query and revision-guard save latency under concurrency.'

    def add_arguments(self, parser):
        parser.add_argument('--tasks', type=int, default=1000)
        parser.add_argument('--annotators', type=int, default=25)
        parser.add_argument('--project', type=int, help='Reuse an existing project id instead of creating a scratch one')

    def handle(self, *args, **options):
        n_tasks, n_workers = options['tasks'], options['annotators']
        if options['project']:
            project = Project.objects.get(pk=options['project'])
        else:
            ws, _ = Workspace.objects.get_or_create(name='Load Test Lab')
            project, _ = Project.objects.get_or_create(workspace=ws, name='Load test')
            Label.objects.get_or_create(project=project, key='load', defaults={'name': 'Load', 'attributes': []})
        label = project.labels.first()

        t0 = time.perf_counter()
        tasks = [Task(project=project, filename=f'load-{i}.png', digest=f'load-{i}-{time.time_ns()}', width=10, height=10) for i in range(n_tasks)]
        Task.objects.bulk_create(tasks, batch_size=500)
        self.stdout.write(f'Created {n_tasks} tasks in {time.perf_counter() - t0:.2f}s')

        annotation = {'regions': [{'id': 'a', 'label': str(label.pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': {}}], 'relations': []}
        ids = list(project.tasks.values_list('id', flat=True))
        latencies = []

        def save_one(task_id):
            try:
                start = time.perf_counter()
                updated = Task.objects.filter(pk=task_id).update(annotation=annotation, state='in_progress', revision=1)
                latencies.append((time.perf_counter() - start) * 1000)
                return updated
            finally:
                close_old_connections()

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            results = list(pool.map(save_one, ids))
        elapsed = time.perf_counter() - t0
        latencies.sort()
        self.stdout.write(
            f'{n_workers} concurrent saves: {sum(results)}/{n_tasks} applied in {elapsed:.2f}s '
            f'({n_tasks / elapsed:.0f} saves/s; p50={latencies[len(latencies) // 2]:.1f}ms '
            f'p95={latencies[int(len(latencies) * 0.95)]:.1f}ms p99={latencies[-1]:.1f}ms)'
        )

        t0 = time.perf_counter()
        list(project.tasks.filter(state='in_progress').values_list('id', flat=True)[:50])
        queue_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        project.tasks.filter(state='submitted').order_by('id').first()
        review_ms = (time.perf_counter() - t0) * 1000
        self.stdout.write(f'Queue page query: {queue_ms:.1f}ms | Review-next query: {review_ms:.1f}ms')
        self.stdout.write(f'Total in project: {project.tasks.count()} tasks')
