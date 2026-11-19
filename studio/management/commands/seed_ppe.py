import io
import json
import random

from PIL import Image, ImageDraw
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.test import Client

from studio.models import Label, Project, Task, Workspace

PASSWORD = 'Demo-2026!'
W, H = 960, 640

USERS = [('annotator1', 'annotator'), ('annotator2', 'annotator'), ('reviewer', 'reviewer')]

PROJECTS = [
    {
        'name': 'Helmet Detection',
        'instructions': 'Box every worker head. Label "helmet" when a hard hat is worn, "no_helmet" when bare-headed.',
        'labels': [
            {'key': 'helmet', 'name': 'Helmet', 'color': '#2e9e4f', 'attributes': 'color:yellow|white|red'},
            {'key': 'no_helmet', 'name': 'No helmet', 'color': '#dc3c3c', 'attributes': ''},
        ],
        'scene': 'helmet',
        'count': 6,
    },
    {
        'name': 'Fire Detection',
        'instructions': 'Box flames with "fire" and smoke plumes with "smoke". Mark severity on fire.',
        'labels': [
            {'key': 'fire', 'name': 'Fire', 'color': '#e05000', 'attributes': 'severity:low|medium|high'},
            {'key': 'smoke', 'name': 'Smoke', 'color': '#808080', 'attributes': ''},
        ],
        'scene': 'fire',
        'count': 6,
    },
]


def helmet_scene(index):
    rng = random.Random(index)
    img = Image.new('RGB', (W, H), (188, 196, 200))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 120, W, H], fill=(120, 110, 100))
    d.rectangle([40, 80, 300, 300], fill=(170, 160, 150))
    d.rectangle([640, 40, 920, 260], fill=(165, 175, 165))
    regions = []
    helmet_colors = {'yellow': (240, 200, 40), 'white': (245, 245, 245), 'red': (220, 60, 50)}
    for i in range(2 + (index % 2)):
        bx = 140 + i * 300 + rng.randint(-40, 40)
        by = 300 + rng.randint(-20, 40)
        d.rectangle([bx - 35, by + 40, bx + 35, by + 170], fill=(60 + i * 30, 90, 160))
        has_helmet = rng.random() < 0.7
        skin = (205, 160, 120)
        if has_helmet:
            color = rng.choice(list(helmet_colors))
            d.ellipse([bx - 28, by - 30, bx + 28, by + 25], fill=helmet_colors[color], outline=(40, 40, 40), width=2)
            d.ellipse([bx - 20, by - 5, bx + 20, by + 28], fill=skin)
            regions.append({'label_key': 'helmet', 'type': 'box', 'pixels': [(bx - 30, by - 32), (bx + 30, by + 28)],
                            'attrs': {'color': color}})
        else:
            d.ellipse([bx - 22, by - 20, bx + 22, by + 28], fill=skin, outline=(40, 40, 40), width=2)
            regions.append({'label_key': 'no_helmet', 'type': 'box', 'pixels': [(bx - 24, by - 22), (bx + 24, by + 30)],
                            'attrs': {}})
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=90)
    return buf.getvalue(), regions


def fire_scene(index):
    rng = random.Random(index)
    img = Image.new('RGB', (W, H), (35, 30, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 380, 900, 560], fill=(90, 70, 55))
    d.rectangle([80, 120, 420, 380], fill=(60, 55, 60))
    regions = []
    for i in range(1 + (index % 2)):
        fx = 250 + i * 350 + rng.randint(-50, 50)
        fy = 400 + rng.randint(-20, 20)
        d.polygon([(fx, fy), (fx + 90, fy - 160), (fx + 130, fy - 60), (fx + 180, fy)], fill=(230, 80, 10))
        d.polygon([(fx + 35, fy), (fx + 90, fy - 95), (fx + 145, fy)], fill=(250, 180, 40))
        regions.append({'label_key': 'fire', 'type': 'box', 'pixels': [(fx, fy - 165), (fx + 180, fy)],
                        'attrs': {'severity': rng.choice(['low', 'medium', 'high'])}})
        sx = fx + 60 + rng.randint(-10, 10)
        for j in range(3):
            d.ellipse([sx - 45 - j * 12, fy - 220 - j * 55, sx + 45 + j * 12, fy - 165 - j * 55], fill=(70 + j * 15,) * 3)
        regions.append({'label_key': 'smoke', 'type': 'box', 'pixels': [(sx - 70, max(0, fy - 390)), (sx + 70, fy - 165)],
                        'attrs': {}})
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=90)
    return buf.getvalue(), regions


SCENES = {'helmet': helmet_scene, 'fire': fire_scene}


def annotation_doc(regions, label_ids):
    return {'regions': [
        {'id': f'r{i}', 'label': str(label_ids[r['label_key']]), 'type': r['type'],
         'points': [[round(x / W, 4), round(y / H, 4)] for x, y in r['pixels']],
         'attributes': r['attrs']}
        for i, r in enumerate(regions)], 'relations': []}


class Command(BaseCommand):
    help = 'Seed PPE demo: Helmet Detection + Fire Detection projects with annotator/reviewer users and a worked role flow.'

    def handle(self, *args, **options):
        User = get_user_model()
        owner = User.objects.filter(username='demo_owner').first()
        if not owner:
            raise SystemExit('Run seed_demo first (needs demo_owner + Demo Lab workspace).')
        workspace = Workspace.objects.get(name='Demo Lab')
        owner_client = Client()
        owner_client.force_login(owner)
        checks = []

        def post(client, path, data, multipart=False, expect=None):
            resp = client.post(path, data) if multipart else client.post(path, json.dumps(data), content_type='application/json')
            if expect is not None:
                ok = resp.status_code == expect
                checks.append(f"{'PASS' if ok else 'FAIL'} {path} -> {resp.status_code} (expected {expect})")
                return resp if ok else None
            if resp.status_code >= 400:
                raise SystemExit(f'{path} -> {resp.status_code}: {resp.content[:300]}')
            return resp

        for username, _ in USERS:
            User.objects.filter(username=username).delete()
        Project.objects.filter(name__in=[p['name'] for p in PROJECTS]).delete()

        member_clients = {}
        for idx, spec in enumerate(PROJECTS):
            annotator_name = USERS[idx][0]
            resp = post(owner_client, '/projects/create/',
                        {'workspace': workspace.pk, 'name': spec['name'], 'instructions': spec['instructions']})
            project_id = int(resp.json()['redirect'].strip('/').split('/')[-1])
            for label in spec['labels']:
                post(owner_client, f'/projects/{project_id}/labels/', label)
            if idx == 0:  # memberships are workspace-level — create once
                for username, role in USERS:
                    post(owner_client, f'/projects/{project_id}/members/',
                         {'username': username, 'password': PASSWORD, 'role': role})
                    c = Client()
                    c.force_login(User.objects.get(username=username))
                    member_clients[username] = c
            annotator = User.objects.get(username=annotator_name)

            scenes = []
            files = []
            for i in range(spec['count']):
                raw, regions = SCENES[spec['scene']](idx * 10 + i)
                scenes.append(regions)
                files.append(SimpleUploadedFile(f"{spec['scene']}_{i:02d}.jpg", raw, content_type='image/jpeg'))
            post(owner_client, f'/projects/{project_id}/upload/', {'images': files}, multipart=True)

            tasks = list(Task.objects.filter(project_id=project_id).order_by('id'))
            label_ids = {l.key: l.pk for l in Label.objects.filter(project_id=project_id)}
            ac = member_clients[annotator_name]
            rc = member_clients['reviewer']

            # worked flow: assign -> annotate -> submit -> review
            for j, task in enumerate(tasks[:3]):
                task.refresh_from_db()
                post(owner_client, f'/tasks/{task.pk}/assign/', {'user': annotator.pk, 'revision': task.revision})
                task.refresh_from_db()
                post(ac, f'/tasks/{task.pk}/save/',
                     {'revision': task.revision, 'annotation': annotation_doc(scenes[j], label_ids), 'submit': True})
                task.refresh_from_db()
                post(rc, f'/tasks/{task.pk}/review/',
                     {'revision': task.revision, 'decision': 'approved' if j < 2 else 'changes_requested',
                      'note': 'Looks good.' if j < 2 else 'Missed one region — recheck the right side.'})

            # role checks
            post(ac, f'/projects/{project_id}/labels/', {'key': 'x', 'name': 'x', 'color': '#000000'}, expect=400)
            task = tasks[0]
            task.refresh_from_db()
            post(ac, f'/tasks/{task.pk}/review/', {'revision': task.revision, 'decision': 'approved'}, expect=403)
            resp = rc.get(f'/projects/{project_id}/')
            checks.append(f"{'PASS' if resp.status_code == 200 else 'FAIL'} reviewer opens project -> {resp.status_code}")
            resp = ac.get(f'/projects/{project_id}/')
            checks.append(f"{'PASS' if resp.status_code == 200 else 'FAIL'} annotator opens project -> {resp.status_code}")

        self.stdout.write(self.style.SUCCESS(
            'PPE demo seeded:\n'
            '  annotator1 / Demo-2026! -> Helmet Detection\n'
            '  annotator2 / Demo-2026! -> Fire Detection\n'
            '  reviewer   / Demo-2026! -> both projects\n'
            'Role checks:\n  ' + '\n  '.join(checks)))
