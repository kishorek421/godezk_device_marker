import io
import json
import random

from PIL import Image, ImageDraw
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.test import Client

from studio.models import Label, Task, Workspace

PASSWORD = 'Demo-2026!'
W, H = 960, 640

LABELS = [
    {'key': 'switch', 'name': 'Switch', 'color': '#206e69', 'attributes': 'state:on|off'},
    {'key': 'indicator', 'name': 'Indicator lamp', 'color': '#dc3c3c', 'attributes': 'color:red|green|amber'},
    {'key': 'cable', 'name': 'Cable run', 'color': '#ca8c06', 'attributes': ''},
]


def make_scene(index):
    """Render a fake control-panel image; returns (jpeg_bytes, regions)."""
    rng = random.Random(index)
    img = Image.new('RGB', (W, H), (52, 63, 72))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([60, 60, W - 60, H - 60], radius=24, fill=(225, 228, 232), outline=(140, 150, 160), width=4)
    regions = []
    for i in range(2):
        x = 150 + i * 300 + rng.randint(-30, 30)
        y = 170 + rng.randint(-25, 25)
        d.rounded_rectangle([x, y, x + 160, y + 240], radius=18,
                            fill=(32, 110, 105) if i == 0 else (92, 55, 158), outline=(20, 20, 20), width=3)
        d.rectangle([x + 60, y + 80, x + 100, y + 160], fill=(240, 240, 240))
        regions.append({'label_key': 'switch', 'type': 'box', 'pixels': [(x, y), (x + 160, y + 240)],
                        'attrs': {'state': 'on' if i == 0 else 'off'}})
    pts = [(200, 500), (310, 450), (430, 485), (560, 445), (575, 480), (435, 525), (305, 490)]
    d.polygon(pts, fill=(202, 140, 6))
    xs, ys = zip(*pts)
    regions.append({'label_key': 'cable', 'type': 'box', 'pixels': [(min(xs), min(ys)), (max(xs), max(ys))], 'attrs': {}})
    cx, cy = 770, 200 + rng.randint(-15, 15)
    d.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(220, 60, 60), outline=(90, 20, 20), width=3)
    regions.append({'label_key': 'indicator', 'type': 'box', 'pixels': [(cx - 18, cy - 18), (cx + 18, cy + 18)], 'attrs': {'color': 'red'}})
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=92)
    return buf.getvalue(), regions


def annotation_doc(regions, label_ids, relations=True):
    doc = {'regions': [], 'relations': []}
    ids = []
    for i, r in enumerate(regions):
        rid = f'r{i}'
        ids.append((rid, r['label_key']))
        doc['regions'].append({'id': rid, 'label': str(label_ids[r['label_key']]), 'type': r['type'],
                               'points': [[round(x / W, 4), round(y / H, 4)] for x, y in r['pixels']],
                               'attributes': r['attrs']})
    if relations:
        cable = next((rid for rid, k in ids if k == 'cable'), None)
        for rid, key in ids:
            if key == 'switch' and cable:
                doc['relations'].append({'from': rid, 'to': cable, 'key': 'feeds'})
        for rid, key in ids:
            if key == 'indicator' and cable:
                doc['relations'].append({'from': rid, 'to': cable, 'key': 'monitors'})
    return doc


class Command(BaseCommand):
    help = 'Seed demo data driving the real API: workspace, members, labels, uploads, annotations, review, release.'

    def add_arguments(self, parser):
        parser.add_argument('--if-empty', action='store_true',
                            help='Seed only when the user table is empty (safe for container startup)')

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', password='Admin2026!')
            self.stdout.write('Created superuser: admin')
        if options['if_empty'] and User.objects.exclude(username='admin').exists():
            self.stdout.write('Users already exist — skipping demo seed.')
            return
        Workspace.objects.filter(name='Demo Lab').delete()
        User.objects.filter(username__startswith='demo_').delete()

        owner = User.objects.create_user('demo_owner', password=PASSWORD)
        clients = {name: Client() for name in ('demo_owner', 'demo_annotator', 'demo_reviewer')}
        clients['demo_owner'].force_login(owner)

        def post(client, path, data, multipart=False):
            if multipart:
                return client.post(path, data)
            response = client.post(path, json.dumps(data), content_type='application/json')
            if response.status_code >= 400:
                raise SystemExit(f'{path} -> {response.status_code}: {response.content[:300]}')
            return response

        post(clients['demo_owner'], '/workspaces/create/', {'name': 'Demo Lab'})
        workspace = Workspace.objects.get(name='Demo Lab')
        response = post(clients['demo_owner'], '/projects/create/',
                        {'workspace': workspace.pk, 'name': 'Control panel inspection',
                         'instructions': 'Box every switch, mark indicator lamps with a point, trace cable runs with polygons. Link switch to cable with "feeds".'})
        project_id = int(response.json()['redirect'].strip('/').split('/')[-1])

        for label in LABELS:
            post(clients['demo_owner'], f'/projects/{project_id}/labels/', label)

        for username, role in [('demo_annotator', 'annotator'), ('demo_reviewer', 'reviewer')]:
            post(clients['demo_owner'], f'/projects/{project_id}/members/',
                 {'username': username, 'password': PASSWORD, 'role': role})
            clients[username].force_login(User.objects.get(username=username))
        annotator = User.objects.get(username='demo_annotator')

        scenes = []
        for batch in range(2):
            files = []
            for i in range(4):
                index = batch * 4 + i
                raw, regions = make_scene(index)
                scenes.append(regions)
                files.append(SimpleUploadedFile(f'panel_{index:02d}.jpg', raw, content_type='image/jpeg'))
            post(clients['demo_owner'], f'/projects/{project_id}/upload/', {'images': files}, multipart=True)

        tasks = list(Task.objects.filter(project_id=project_id).order_by('id'))
        label_ids = {l.key: l.pk for l in Label.objects.filter(project_id=project_id)}

        def save(task, submit=False, relations=True):
            task.refresh_from_db()
            post(clients['demo_annotator'], f'/tasks/{task.pk}/save/',
                 {'revision': task.revision,
                  'annotation': annotation_doc(scenes[tasks.index(task)], label_ids, relations), 'submit': submit})

        for task in tasks[:2]:
            task.refresh_from_db()
            post(clients['demo_owner'], f'/tasks/{task.pk}/assign/', {'user': annotator.pk, 'revision': task.revision})
            save(task, submit=True)
            task.refresh_from_db()
            post(clients['demo_reviewer'], f'/tasks/{task.pk}/review/',
                 {'revision': task.revision, 'decision': 'approved', 'note': 'Geometry and labels verified.'})
        task = tasks[2]
        task.refresh_from_db()
        post(clients['demo_owner'], f'/tasks/{task.pk}/assign/', {'user': annotator.pk, 'revision': task.revision})
        save(task, submit=True, relations=False)
        task.refresh_from_db()
        post(clients['demo_reviewer'], f'/tasks/{task.pk}/review/',
             {'revision': task.revision, 'decision': 'changes_requested',
              'note': 'Missing switch-to-cable "feeds" relations. Add and resubmit.'})
        task = tasks[3]
        task.refresh_from_db()
        post(clients['demo_owner'], f'/tasks/{task.pk}/assign/', {'user': annotator.pk, 'revision': task.revision})
        save(task)
        task = tasks[4]
        post(clients['demo_owner'], f'/tasks/{task.pk}/assign/', {'user': annotator.pk, 'revision': 0})

        post(clients['demo_owner'], f'/projects/{project_id}/releases/', {'name': 'demo-v1'})

        self.stdout.write(self.style.SUCCESS(f'''
Demo data seeded. Log in at /accounts/login/ with any of:

  admin          / Admin2026!   (superuser - Django admin)
  demo_owner     / {PASSWORD}   (workspace owner)
  demo_annotator / {PASSWORD}   (annotator - assigned tasks)
  demo_reviewer  / {PASSWORD}   (reviewer - approve/change-request)

Project "Control panel inspection": 8 tasks -
  2 approved (exported in release "demo-v1", try the COCO download)
  1 changes_requested (see the review note)
  1 in_progress draft, 1 assigned, 3 open
'''))
