from django.test import TestCase, TransactionTestCase

# Create your tests here.

import concurrent.futures
import hashlib
import hmac
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from datetime import timedelta
from unittest import mock
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.signals import request_finished
from django.db import close_old_connections
from django.test import override_settings, Client
from django.utils import timezone
from .models import Workspace, Membership, Project, Label, Task, Release, Event, ApiToken, Webhook, WebhookDelivery, Job
from .jobs import run_pending


def close_response(response):
    """Close a streamed response without firing request_finished's
    close_old_connections, which would kill the connection inside a
    TestCase's atomic block on file-backed test databases."""
    request_finished.disconnect(close_old_connections)
    try:
        response.close()
    finally:
        request_finished.connect(close_old_connections)


class WorkflowTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.override.disable)
        User = get_user_model()
        self.owner = User.objects.create_user('owner', password='Test-owner-923!')
        self.worker = User.objects.create_user('worker', password='Test-worker-923!')
        self.reviewer = User.objects.create_user('reviewer', password='Test-reviewer-923!')
        self.outsider = User.objects.create_user('outsider', password='Test-outside-923!')
        self.workspace = Workspace.objects.create(name='Lab')
        for user, role in [(self.owner, 'owner'), (self.worker, 'annotator'), (self.reviewer, 'reviewer')]:
            Membership.objects.create(workspace=self.workspace, user=user, role=role)
        self.project = Project.objects.create(workspace=self.workspace, name='Switches')
        self.label = Label.objects.create(project=self.project, name='Switch', key='switch', attributes=['state'])
        self.client.force_login(self.owner)

    def post(self, path, data):
        return self.client.post(path, json.dumps(data), content_type='application/json')

    def image(self, name='sample.png', color='red'):
        buffer = io.BytesIO()
        Image.new('RGB', (120, 80), color).save(buffer, 'PNG')
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')

    def upload(self):
        result = self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image()})
        self.assertEqual(result.status_code, 200, result.content)
        return self.project.tasks.get()

    def annotation(self):
        return {'regions': [
            {'id': 'a', 'label': str(self.label.pk), 'type': 'box', 'points': [[.1, .1], [.4, .6]], 'attributes': {'state': 'on'}},
            {'id': 'b', 'label': str(self.label.pk), 'type': 'box', 'points': [[.6, .6], [.9, .9]], 'attributes': {}}
        ], 'relations': [{'from': 'a', 'to': 'b', 'key': 'controls'}]}

    def test_end_to_end_review_export_and_immutable_snapshot(self):
        task = self.upload()
        response = self.post(f'/tasks/{task.pk}/assign/', {'user': self.worker.pk, 'revision': 0})
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.worker)
        response = self.post(f'/tasks/{task.pk}/save/', {'revision': 1, 'annotation': self.annotation()})
        self.assertEqual(response.json()['revision'], 2)
        task.refresh_from_db()
        self.assertEqual(task.annotation['regions'][0]['attributes']['state'], 'on')
        response = self.post(f'/tasks/{task.pk}/save/', {'revision': 2, 'annotation': self.annotation(), 'submit': True})
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.reviewer)
        response = self.post(f'/tasks/{task.pk}/review/', {'revision': 3, 'decision': 'approved', 'note': 'Looks correct'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post(f'/projects/{self.project.pk}/releases/', {'name': 'v1'}).status_code, 200)
        release = Release.objects.get()
        exported = self.client.get(f'/releases/{release.pk}/').json()
        self.assertEqual(exported['tasks'][0]['annotation'], self.annotation())
        self.assertTrue(exported['provenance'])
        Label.objects.filter(pk=self.label.pk).update(name='Renamed')
        self.assertEqual(self.client.get(f'/releases/{release.pk}/').json(), exported)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 4, 'annotation': self.annotation()}).status_code, 403)

    def test_cross_workspace_isolation(self):
        task = self.upload()
        release = Release.objects.create(project=self.project, name='test', snapshot={})
        self.client.force_login(self.outsider)
        for path in [f'/projects/{self.project.pk}/', f'/tasks/{task.pk}/', f'/tasks/{task.pk}/asset/', f'/releases/{release.pk}/']:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for path, data in [(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()}), (f'/projects/{self.project.pk}/labels/', {'name': 'bad', 'key': 'bad'})]:
            self.assertEqual(self.post(path, data).status_code, 404)

    def test_role_restrictions(self):
        task = self.upload()
        self.client.force_login(self.worker)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()}).status_code, 403)
        self.assertEqual(self.post(f'/tasks/{task.pk}/assign/', {'user': self.worker.pk, 'revision': 0}).status_code, 403)
        self.assertEqual(self.post(f'/projects/{self.project.pk}/labels/', {'name': 'bad', 'key': 'bad'}).status_code, 400)
        self.assertEqual(self.post(f'/projects/{self.project.pk}/releases/', {'name': 'bad'}).status_code, 400)

    def test_revision_conflict_preserves_latest_data(self):
        task = self.upload()
        body = {'revision': 0, 'annotation': self.annotation()}
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', body).status_code, 200)
        body['annotation'] = {'regions': [], 'relations': []}
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', body).status_code, 409)
        task.refresh_from_db()
        self.assertEqual(len(task.annotation['regions']), 2)
        self.assertEqual(Event.objects.filter(action='annotation.saved').count(), 1)

    def test_malformed_annotations_are_rejected_without_save(self):
        task = self.upload()
        invalid = []
        for points in [[[-1, 0], [.5, .5]], [[.8, .8], [.2, .2]], [[0, 0]], [[0, 0], [float('nan'), 1]], [[False, 0], [1, 1]]]:
            doc = self.annotation(); doc['regions'][0]['points'] = points; invalid.append(doc)
        doc = self.annotation(); doc['regions'][0]['label'] = 'not-in-project'; invalid.append(doc)
        doc = self.annotation(); doc['regions'][0]['attributes'] = {'unknown': 'value'}; invalid.append(doc)
        doc = self.annotation(); doc['relations'][0]['to'] = 'missing'; invalid.append(doc)
        doc = self.annotation(); doc['regions'][1]['id'] = 'a'; invalid.append(doc)
        invalid.extend([None, [], {'regions': 'bad'}, {'regions': [None]}])
        for value in invalid:
            result = self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': value})
            self.assertEqual(result.status_code, 400, result.content)
        task.refresh_from_db(); self.assertEqual(task.revision, 0)

    def test_polygon_is_supported(self):
        task = self.upload()
        doc = self.annotation()
        doc['regions'][0]['type'] = 'polygon'; doc['regions'][0]['points'] = [[0, 0], [1, 0], [.5, 1]]
        doc['regions'][1]['type'] = 'polygon'; doc['regions'][1]['points'] = [[.2, .2], [.4, .2], [.3, .4]]
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': doc}).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.geometry, 'polygon')

    def test_review_rejection_and_resubmission(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation(), 'submit': True})
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'approved'}).status_code, 403)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'changes_requested'}).status_code, 400)
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'changes_requested', 'note': 'Tighten box'}).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 2, 'annotation': self.annotation(), 'submit': True}).status_code, 200)

    def test_geometry_locks_to_first_saved_type(self):
        task = self.upload()
        box = {'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'box', 'points': [[.1, .1], [.4, .6]], 'attributes': {}}], 'relations': []}
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': box}).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.geometry, 'box')
        point = {'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': {}}], 'relations': []}
        task.refresh_from_db()
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': task.revision, 'annotation': point}).status_code, 400)
        # A fresh project still rejects a mixed first annotation
        other = Project.objects.create(workspace=self.workspace, name='Other')
        Label.objects.create(project=other, key='l', name='L')
        t2 = Task.objects.create(project=other, filename='t.png', digest='t1', width=10, height=10, image='x.jpg')
        mixed = {'regions': [
            {'id': 'a', 'label': str(other.labels.get().pk), 'type': 'box', 'points': [[.1, .1], [.4, .6]], 'attributes': {}},
            {'id': 'b', 'label': str(other.labels.get().pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': {}}], 'relations': []}
        self.assertEqual(self.post(f'/tasks/{t2.pk}/save/', {'revision': 0, 'annotation': mixed}).status_code, 400)
        other.refresh_from_db()
        self.assertEqual(other.geometry, 'any')

    def test_owner_can_change_geometry_lock(self):
        task = self.upload()
        box = {'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'box', 'points': [[.1, .1], [.4, .6]], 'attributes': {}}], 'relations': []}
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': box})
        url = f'/projects/{self.project.pk}/geometry/'
        self.client.force_login(self.worker)
        self.assertEqual(self.post(url, {'geometry': 'any'}).status_code, 400)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(url, {'geometry': 'bogus'}).status_code, 400)
        self.assertEqual(self.post(url, {'geometry': 'any'}).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.geometry, 'any')

    def test_editor_prev_next_navigation(self):
        for i in range(3):
            Task.objects.create(project=self.project, filename=f'n{i}.jpg', digest=f'nav{i}', width=10, height=10, image='x.jpg')
        ids = list(self.project.tasks.values_list('id', flat=True).order_by('id'))
        middle = ids[1]
        html = self.client.get(f'/tasks/{middle}/').content.decode()
        self.assertIn(f'/tasks/{ids[0]}/', html)
        self.assertIn(f'/tasks/{ids[2]}/', html)
        self.assertIn('2/3', html)
        first = self.client.get(f'/tasks/{ids[0]}/').content.decode()
        self.assertNotIn('← Prev', first)

    def test_upload_with_assignee(self):
        result = self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image(), 'assign_to': self.worker.pk})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.project.tasks.get().assignee, self.worker)
        bad = self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image('other.png', 'blue'), 'assign_to': self.outsider.pk})
        self.assertEqual(bad.status_code, 404)

    def test_export_zip_contains_labeled_tasks_and_images(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()})
        self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image('second.png', 'blue')})
        url = f'/projects/{self.project.pk}/export/'
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.reviewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content)
        close_response(response)
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = archive.namelist()
        self.assertIn('annotations.json', names)
        self.assertTrue(any(n.startswith('images/') for n in names))
        data = json.loads(archive.read('annotations.json'))
        self.assertTrue(data['draft'])
        self.assertEqual(len(data['tasks']), 1)
        self.assertEqual(len(data['tasks'][0]['annotation']['regions']), 2)
        self.assertEqual(data['tasks'][0]['state'], 'in_progress')
        empty = Project.objects.create(workspace=self.workspace, name='Empty')
        self.assertEqual(self.client.get(f'/projects/{empty.pk}/export/').status_code, 400)

    def test_task_comments_and_history_restore(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()})
        task.refresh_from_db()
        smaller = {'regions': [self.annotation()['regions'][0]], 'relations': []}
        self.post(f'/tasks/{task.pk}/save/', {'revision': task.revision, 'annotation': smaller})
        self.client.force_login(self.worker)
        self.assertEqual(self.post(f'/tasks/{task.pk}/comment/', {'note': 'Second box is blurry'}).status_code, 200)
        self.assertTrue(Event.objects.filter(task=task, action='task.comment').exists())
        task.refresh_from_db()
        self.assertEqual(self.post(f'/tasks/{task.pk}/restore/', {'revision': task.revision, 'event_id': 1}).status_code, 403)
        self.client.force_login(self.owner)
        source = Event.objects.filter(task=task, action='annotation.saved').order_by('id').first()
        self.assertEqual(self.post(f'/tasks/{task.pk}/restore/', {'revision': task.revision, 'event_id': source.pk}).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(len(task.annotation['regions']), 2)
        self.assertEqual(self.post(f'/tasks/{task.pk}/restore/', {'revision': task.revision - 1, 'event_id': source.pk}).status_code, 409)

    def test_review_queue_auto_advances_and_spot_check(self):
        t1 = self.upload()
        self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image('two.png', 'blue')})
        t2 = self.project.tasks.order_by('id').last()
        for t in (t1, t2):
            self.post(f'/tasks/{t.pk}/save/', {'revision': 0, 'annotation': self.annotation(), 'submit': True})
        url = f'/projects/{self.project.pk}/review-next/'
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(url)['Location'], f'/tasks/{t1.pk}/')
        t1.refresh_from_db()
        r = self.post(f'/tasks/{t1.pk}/review/', {'decision': 'approved', 'note': '', 'revision': t1.revision})
        self.assertEqual(r.json()['redirect'], f'/tasks/{t2.pk}/')
        t2.refresh_from_db()
        r = self.post(f'/tasks/{t2.pk}/review/', {'decision': 'approved', 'note': '', 'revision': t2.revision})
        self.assertEqual(r.json()['redirect'], f'/projects/{self.project.pk}/')
        r = self.client.get(f'/projects/{self.project.pk}/spot-check/')
        self.assertIn(r['Location'], [f'/tasks/{t1.pk}/', f'/tasks/{t2.pk}/'])
        self.assertTrue(Event.objects.filter(action='quality.sampled').exists())
        empty = Project.objects.create(workspace=self.workspace, name='Empty')
        self.assertEqual(self.client.get(f'/projects/{empty.pk}/spot-check/')['Location'], f'/projects/{empty.pk}/')

    def test_distribute_evenly_splits_tasks(self):
        worker2 = get_user_model().objects.create_user('worker2', password='Test-worker-923!')
        Membership.objects.create(workspace=self.workspace, user=worker2, role='annotator')
        self.upload()
        for name, color in [('two.png', 'blue'), ('three.png', 'green'), ('four.png', 'white'), ('five.png', 'black')]:
            self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image(name, color)})
        url = f'/projects/{self.project.pk}/distribute/'
        self.client.force_login(self.worker)
        self.assertEqual(self.post(url, {'user_ids': [self.worker.pk]}).status_code, 400)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(url, {'user_ids': [], 'scope': 'unassigned'}).status_code, 400)
        self.assertEqual(self.post(url, {'user_ids': [self.worker.pk], 'scope': 'bogus'}).status_code, 400)
        # Non-annotator members cannot receive distributed tasks
        self.assertEqual(self.post(url, {'user_ids': [self.worker.pk, self.reviewer.pk], 'scope': 'unassigned'}).status_code, 400)
        r = self.post(url, {'user_ids': [self.worker.pk, worker2.pk], 'scope': 'unassigned'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Task.objects.filter(project=self.project, assignee=self.worker).count(), 3)
        self.assertEqual(Task.objects.filter(project=self.project, assignee=worker2).count(), 2)
        self.assertTrue(Event.objects.filter(project=self.project, action='tasks.distributed').exists())
        r = self.post(url, {'user_ids': [self.worker.pk], 'scope': 'unassigned'})
        self.assertEqual(r.status_code, 400)
        for name, color in [('six.png', 'cyan'), ('seven.png', 'gray')]:
            self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image(name, color)})
        r = self.post(url, {'user_ids': [self.worker.pk, worker2.pk], 'scope': 'unassigned', 'shuffle': True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Task.objects.filter(project=self.project, assignee=self.worker).count(), 4)
        self.assertEqual(Task.objects.filter(project=self.project, assignee=worker2).count(), 3)

    def test_progress_report_on_project_page(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation(), 'submit': True})
        response = self.client.get(f'/projects/{self.project.pk}/')
        progress = response.context['progress']
        self.assertEqual(progress['submitted_7d'], 1)
        self.assertEqual(progress['rows'][0]['username'], 'owner')
        self.assertEqual(progress['rows'][0]['submitted'], 1)
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(f'/projects/{self.project.pk}/'), 'Progress</h2>')

    def test_label_archive_lifecycle(self):
        task = self.upload()
        url = f'/projects/{self.project.pk}/labels/{self.label.pk}/archive/'
        self.client.force_login(self.worker)
        self.assertEqual(self.post(url, {}).status_code, 400)
        self.client.force_login(self.owner)
        version = self.project.schema_version
        self.assertEqual(self.post(url, {}).status_code, 200)
        self.label.refresh_from_db(); self.project.refresh_from_db()
        self.assertTrue(self.label.archived)
        self.assertEqual(self.project.schema_version, version + 1)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()}).status_code, 400)
        self.assertEqual(self.post(url, {}).status_code, 200)
        self.assertFalse(Label.objects.get(pk=self.label.pk).archived)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()}).status_code, 200)

    def _release(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation(), 'submit': True})
        task.refresh_from_db()
        self.client.force_login(self.reviewer)
        self.post(f'/tasks/{task.pk}/review/', {'decision': 'approved', 'note': '', 'revision': task.revision})
        self.post(f'/projects/{self.project.pk}/releases/', {'name': 'v1'})
        self.client.force_login(self.owner)
        return self.project.releases.get()

    def test_release_yolo_zip(self):
        release = self._release()
        response = self.client.get(f'/releases/{release.pk}/yolo/')
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content)
        close_response(response)
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = archive.namelist()
        self.assertIn('data.yaml', names)
        self.assertIn('Switch', archive.read('data.yaml').decode())
        label_file = [n for n in names if n.startswith('labels/')][0]
        self.assertIn('0 0.250000 0.350000 0.300000 0.500000', archive.read(label_file).decode())
        image_file = [n for n in names if n.startswith('images/')][0]
        self.assertEqual(label_file[7:-4], image_file[7:-4])
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(f'/releases/{release.pk}/yolo/').status_code, 200)

    def test_api_tokens_and_read_api(self):
        task = self.upload()
        secret = self.post(f'/projects/{self.project.pk}/tokens/', {'name': 'pipeline'}).json()['token']
        self.assertTrue(secret.startswith('gm_'))
        headers = {'HTTP_AUTHORIZATION': f'Bearer {secret}'}
        self.client.logout()
        self.assertEqual(self.client.get('/api/v1/projects/', **headers).status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/projects/{self.project.pk}/', **headers).status_code, 200)
        tasks = self.client.get(f'/api/v1/projects/{self.project.pk}/tasks/', **headers).json()
        self.assertEqual(tasks['total'], 1)
        self.assertEqual(self.client.get(f'/api/v1/tasks/{task.pk}/', **headers).json()['id'], task.pk)
        other = Project.objects.create(workspace=Workspace.objects.create(name='Other'), name='X')
        self.assertEqual(self.client.get(f'/api/v1/projects/{other.pk}/', **headers).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/projects/', HTTP_AUTHORIZATION='Bearer nope').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/projects/').status_code, 401)
        self.client.force_login(self.owner)
        token = ApiToken.objects.get()
        self.post(f'/projects/{self.project.pk}/tokens/{token.pk}/revoke/', {})
        self.assertEqual(self.client.get('/api/v1/projects/', **headers).status_code, 401)

    def test_webhook_crud_and_signed_delivery(self):
        url = f'/projects/{self.project.pk}/webhooks/'
        self.client.force_login(self.worker)
        self.assertEqual(self.post(url, {'url': 'https://x.test/hook'}).status_code, 400)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(url, {'url': 'notaurl'}).status_code, 400)
        self.assertEqual(self.post(url, {'url': 'https://x.test/hook'}).status_code, 200)
        hook = self.project.webhooks.get()
        task = self.upload()
        with mock.patch('studio.jobs.urllib.request.urlopen') as fake:
            fake.return_value.__enter__.return_value.status = 200
            with self.captureOnCommitCallbacks(execute=True):
                self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation()})
        req = fake.call_args[0][0]
        expected = hmac.new(hook.secret.encode(), req.data, hashlib.sha256).hexdigest()
        self.assertEqual(req.headers['X-godezk-signature'], f'sha256={expected}')
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.status, 'delivered')
        self.assertEqual(delivery.response_code, 200)
        with mock.patch('studio.jobs.urllib.request.urlopen', side_effect=OSError('down')):
            with self.captureOnCommitCallbacks(execute=True):
                self.post(f'/tasks/{task.pk}/comment/', {'note': 'hi'})
        self.assertEqual(WebhookDelivery.objects.filter(status='failed').count(), 1)
        self.assertEqual(self.post(f'/projects/{self.project.pk}/webhooks/{hook.pk}/delete/', {}).status_code, 200)
        self.assertEqual(self.project.webhooks.count(), 0)

    def test_failed_webhook_is_retried_by_worker(self):
        Webhook.objects.create(project=self.project, url='https://hook.test/x', secret='s')
        task = self.upload()
        with mock.patch('studio.jobs.urllib.request.urlopen', side_effect=OSError('down')):
            with self.captureOnCommitCallbacks(execute=True):
                self.post(f'/tasks/{task.pk}/assign/', {'user': self.worker.pk, 'revision': 0})
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.status, 'failed')
        job = Job.objects.get()
        self.assertEqual((job.type, job.status), ('webhook.deliver', 'pending'))
        with mock.patch('studio.jobs.urllib.request.urlopen') as fake:
            fake.return_value.__enter__.return_value.status = 200
            self.assertEqual(run_pending(), 1)
        delivery.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(delivery.status, 'delivered')
        self.assertEqual(job.status, 'done')

    def test_permanently_failing_job_is_dead_lettered(self):
        Webhook.objects.create(project=self.project, url='https://hook.test/x', secret='s')
        delivery = WebhookDelivery.objects.create(webhook=self.project.webhooks.get(), action='x', status='failed')
        job = Job.objects.create(type='webhook.deliver', payload={'delivery_id': str(delivery.pk), 'body': {'event': 'x'}}, max_attempts=2)
        with mock.patch('studio.jobs.urllib.request.urlopen', side_effect=OSError('down')):
            self.assertEqual(run_pending(), 1)
            job.refresh_from_db()
            self.assertEqual(job.status, 'pending')  # first failure → scheduled retry
            self.assertEqual(run_pending(), 0)       # backoff holds it
            job.run_after = timezone.now()
            job.save(update_fields=['run_after'])
            self.assertEqual(run_pending(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, 'dead')
        self.assertEqual(job.attempts, 2)

    def test_backup_archive_is_restorable(self):
        task = self.upload()
        out = os.path.join(self.directory.name, 'backups')
        call_command('backupdata', out=out)
        archive = os.path.join(out, os.listdir(out)[0])
        with tarfile.open(archive) as tar:
            sql = tar.extractfile('db.sqlite3.sql').read().decode()
            self.assertTrue(any(m.name.startswith('media/') for m in tar.getmembers()))
        scratch = sqlite3.connect(':memory:')
        scratch.executescript(sql)
        count = scratch.execute('SELECT COUNT(*) FROM studio_task').fetchone()[0]
        self.assertEqual(count, self.project.tasks.count())

    def test_health_endpoint(self):
        anon = Client()
        r = anon.get('/health/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'ok')
        self.assertNotIn('tasks', r.json())  # detail is privileged
        admin = get_user_model().objects.create_superuser('root', password='Test-root-923!')
        anon.force_login(admin)
        r = anon.get('/health/')
        for key in ('projects', 'tasks', 'jobs_pending', 'jobs_dead', 'deliveries_failed'):
            self.assertIn(key, r.json())

    def test_upload_validation_and_duplicate_detection(self):
        self.upload()
        self.assertEqual(self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image()}).status_code, 400)
        bad = SimpleUploadedFile('fake.png', b'<script>bad</script>', content_type='image/png')
        self.assertEqual(self.client.post(f'/projects/{self.project.pk}/upload/', {'images': bad}).status_code, 400)
        self.assertEqual(self.project.tasks.count(), 1)
        # BMP/GIF/TIFF accepted and re-encoded to JPEG
        for fmt in ('BMP', 'GIF', 'TIFF'):
            buf = io.BytesIO(); Image.new('RGB', (20, 20), 'teal').save(buf, fmt)
            r = self.client.post(f'/projects/{self.project.pk}/upload/',
                                 {'images': SimpleUploadedFile(f't.{fmt.lower()}', buf.getvalue())})
            self.assertEqual(r.status_code, 200, f'{fmt}: {r.content}')

    def test_attribute_options_are_enforced(self):
        lamp = Label.objects.create(project=self.project, key='lamp', name='Lamp', attributes=[
            {'key': 'state', 'options': ['on', 'off']}, {'key': 'note', 'options': None}])
        task = self.upload()
        region = lambda attrs: {'regions': [{'id': 'a', 'label': str(lamp.pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': attrs}], 'relations': []}
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': region({'state': 'on', 'note': 'free text'})}).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': task.revision, 'annotation': region({'state': 'dim'})}).status_code, 400)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': task.revision, 'annotation': region({'state': 5})}).status_code, 400)

    def test_label_create_with_options_syntax(self):
        url = f'/projects/{self.project.pk}/labels/'
        result = self.post(url, {'name': 'Dial', 'key': 'dial', 'color': '#aabbcc', 'attributes': 'mode:auto|manual, comment'})
        self.assertEqual(result.status_code, 200, result.content)
        label = self.project.labels.get(key='dial')
        self.assertEqual(label.attribute_defs(), [{'key': 'mode', 'options': ['auto', 'manual']}, {'key': 'comment', 'options': None}])
        for i, bad in enumerate(['mode:a|a', 'mode:' + '|'.join(f'o{i}' for i in range(51)), 'a b:x']):
            self.assertEqual(self.post(url, {'name': 'x', 'key': f'bad{i}', 'attributes': bad}).status_code, 400)

    def test_schema_keys_and_version(self):
        url = f'/projects/{self.project.pk}/labels/'
        data = {'name': 'Device', 'key': 'device', 'color': '#aabbcc', 'attributes': 'state, serial'}
        self.assertEqual(self.post(url, data).status_code, 200)
        self.project.refresh_from_db(); self.assertEqual(self.project.schema_version, 2)
        self.assertEqual(self.post(url, data).status_code, 409)
        self.project.refresh_from_db(); self.assertEqual(self.project.schema_version, 2)
        data['key'] = 'other'; data['attributes'] = 'state,state'
        self.assertEqual(self.post(url, data).status_code, 400)

    def test_team_account_creation_and_password_validation(self):
        url = f'/projects/{self.project.pk}/members/'
        self.assertEqual(self.post(url, {'username': 'newbie', 'password': '123', 'role': 'annotator'}).status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username='newbie').exists())
        self.assertEqual(self.post(url, {'username': 'newbie', 'password': 'Test-new-strong-329!', 'role': 'annotator'}).status_code, 200)
        self.assertTrue(get_user_model().objects.get(username='newbie').check_password('Test-new-strong-329!'))

    def test_csrf_and_authentication(self):
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.owner)
        self.assertEqual(secure_client.post('/workspaces/create/', {'name': 'nope'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.post('/workspaces/create/', {'name': 'nope'}).status_code, 302)

    def test_pages_render_and_assets_are_private(self):
        task = self.upload()
        for path in ['/', f'/projects/{self.project.pk}/', f'/tasks/{task.pk}/']:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        response = self.client.get(f'/tasks/{task.pk}/asset/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        close_response(response)

    def test_bulk_assign(self):
        tasks = [self.upload()]
        for i in range(2):
            buffer = io.BytesIO()
            Image.new('RGB', (60, 40), 'green').save(buffer, 'PNG')
            tasks.append(Task.objects.create(project=self.project, filename=f'b{i}.png', digest=f'bulk{i}', width=60, height=40, image='x.jpg'))
        ids = [t.pk for t in tasks]
        url = f'/projects/{self.project.pk}/assign/'
        result = self.post(url, {'user': self.worker.pk, 'task_ids': ids})
        self.assertEqual(result.status_code, 200, result.content)
        for t in tasks:
            t.refresh_from_db()
            self.assertEqual(t.assignee, self.worker)
            self.assertEqual(t.revision, 1)
        # Submitted/approved tasks are rejected
        tasks[0].state = 'submitted'; tasks[0].save()
        self.assertEqual(self.post(url, {'user': self.reviewer.pk, 'task_ids': ids}).status_code, 400)
        tasks[0].refresh_from_db(); self.assertEqual(tasks[0].assignee, self.worker)
        # Foreign project task id rejected
        other = Project.objects.create(workspace=self.workspace, name='Other')
        foreign = Task.objects.create(project=other, filename='f.png', digest='f1', width=1, height=1, image='x.jpg')
        self.assertEqual(self.post(url, {'user': self.worker.pk, 'task_ids': [foreign.pk]}).status_code, 400)
        # Non-member target rejected
        self.assertEqual(self.post(url, {'user': self.outsider.pk, 'task_ids': ids[:1]}).status_code, 404)
        # Annotator callers rejected
        self.client.force_login(self.worker)
        self.assertEqual(self.post(url, {'user': self.worker.pk, 'task_ids': ids[:1]}).status_code, 400)

    def test_coco_export(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/assign/', {'user': self.worker.pk, 'revision': 0})
        self.client.force_login(self.worker)
        self.post(f'/tasks/{task.pk}/save/', {'revision': 1, 'annotation': self.annotation(), 'submit': True})
        self.client.force_login(self.reviewer)
        self.post(f'/tasks/{task.pk}/review/', {'revision': 2, 'decision': 'approved', 'note': 'ok'})
        self.post(f'/projects/{self.project.pk}/releases/', {'name': 'v1'})
        release = Release.objects.get()
        response = self.client.get(f'/releases/{release.pk}/coco/')
        self.assertIn('coco', response['Content-Disposition'])
        coco = response.json()
        self.assertEqual(coco['images'], [{'id': task.pk, 'file_name': 'sample.png', 'width': 120, 'height': 80}])
        self.assertEqual(coco['categories'], [{'id': 1, 'name': 'Switch', 'supercategory': 'switch'}])
        box = next(a for a in coco['annotations'] if a['region_id'] == 'a')
        other = next(a for a in coco['annotations'] if a['region_id'] == 'b')
        self.assertEqual(box['bbox'], [12, 8, 36, 40])
        self.assertEqual(box['area'], 1440)
        self.assertEqual(box['attributes'], {'state': 'on'})
        self.assertEqual(coco['relations'], [{'from': box['id'], 'to': other['id'], 'key': 'controls', 'image_id': task.pk}])
        self.assertEqual(coco['info']['source_format'], 'annotation-studio/v1')

    def test_coco_maps_points_and_polygons(self):
        from studio.views import snapshot_to_coco
        release = Release.objects.create(project=self.project, name='v', snapshot={})
        snapshot = {'project': 'P', 'labels': [{'id': 'lab1', 'key': 'k', 'name': 'N'}], 'tasks': [
            {'id': 7, 'filename': 'f.png', 'width': 100, 'height': 100, 'annotation': {'regions': [
                {'id': 'p', 'label': 'lab1', 'type': 'point', 'points': [[.5, .5]], 'attributes': {}},
                {'id': 'g', 'label': 'lab1', 'type': 'polygon', 'points': [[0, 0], [1, 0], [.5, 1]], 'attributes': {}}
            ], 'relations': []}}]}
        coco = snapshot_to_coco(snapshot, release)
        point = next(a for a in coco['annotations'] if a['region_id'] == 'p')
        self.assertEqual(point['keypoints'], [50, 50, 2])
        polygon = next(a for a in coco['annotations'] if a['region_id'] == 'g')
        self.assertEqual(polygon['segmentation'], [[0, 0, 100, 0, 50, 100]])
        self.assertEqual(polygon['area'], 5000)

    def test_queue_pagination(self):
        for i in range(60):
            Task.objects.create(project=self.project, filename=f'img{i:03}.jpg', digest=f'd{i:03}', width=10, height=10, image='x.jpg')
        self.assertContains(self.client.get(f'/projects/{self.project.pk}/'), 'Page 1 of 2')
        page2 = self.client.get(f'/projects/{self.project.pk}/?page=2')
        self.assertContains(page2, 'img050')
        self.assertNotContains(page2, 'img000')
        filtered = self.client.get(f'/projects/{self.project.pk}/?q=img05&page=1')
        self.assertContains(filtered, '10 items')

    def test_empty_release_and_bad_json(self):
        self.assertEqual(self.post(f'/projects/{self.project.pk}/releases/', {'name': 'empty'}).status_code, 400)
        self.assertEqual(self.client.post('/workspaces/create/', '{bad', content_type='application/json').status_code, 400)


class IsolationTests(TestCase):
    """Phase 1 exit criterion: a user must never reach another workspace's
    projects, tasks, assets, annotations, or releases — read or write."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.override.disable)
        User = get_user_model()
        self.owner = User.objects.create_user('owner', password='Test-owner-923!')
        self.outsider = User.objects.create_user('outsider', password='Test-outside-923!')
        self.workspace = Workspace.objects.create(name='Lab A')
        self.other_workspace = Workspace.objects.create(name='Lab B')
        Membership.objects.create(workspace=self.workspace, user=self.owner, role='owner')
        Membership.objects.create(workspace=self.other_workspace, user=self.outsider, role='owner')
        self.project = Project.objects.create(workspace=self.workspace, name='Secret project')
        self.label = Label.objects.create(project=self.project, key='device', name='Device', attributes=[{'key': 'state', 'options': ['on', 'off']}])
        buffer = io.BytesIO()
        Image.new('RGB', (40, 30), 'blue').save(buffer, 'PNG')
        self.client.force_login(self.owner)
        self.client.post(f'/projects/{self.project.pk}/upload/', {'images': SimpleUploadedFile('secret.png', buffer.getvalue(), content_type='image/png')})
        self.task = self.project.tasks.get()
        self.release = Release.objects.create(project=self.project, name='v1', snapshot={
            'format': 'annotation-studio/v1', 'project': 'Secret project', 'schema_version': 1,
            'labels': [{'id': str(self.label.pk), 'key': 'device', 'name': 'Device', 'color': '#206e69', 'attributes': []}],
            'tasks': [{'id': self.task.pk, 'filename': 'secret.png', 'digest': self.task.digest,
                       'width': 40, 'height': 30, 'revision': 0, 'annotation': {'regions': [], 'relations': []}}],
            'provenance': []})
        self.client.force_login(self.outsider)

    def get_statuses(self, paths):
        return {path: self.client.get(path).status_code for path in paths}

    def test_no_read_access_to_any_object(self):
        statuses = self.get_statuses([
            f'/projects/{self.project.pk}/',
            f'/tasks/{self.task.pk}/',
            f'/tasks/{self.task.pk}/asset/',
            f'/releases/{self.release.pk}/',
            f'/releases/{self.release.pk}/coco/',
            f'/projects/{self.project.pk}/export/',
            f'/projects/{self.project.pk}/review-next/',
            f'/projects/{self.project.pk}/spot-check/',
        ])
        self.assertTrue(all(code == 404 for code in statuses.values()), statuses)
        self.assertEqual(self.client.get('/api/v1/projects/').status_code, 401)
        self.assertEqual(self.client.get(f'/api/v1/tasks/{self.task.pk}/').status_code, 401)

    def test_no_write_access_to_any_endpoint(self):
        annotation = {'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'box', 'points': [[.1, .1], [.5, .5]], 'attributes': {}}], 'relations': []}
        posts = {
            f'/projects/{self.project.pk}/members/': {'username': 'x', 'role': 'annotator'},
            f'/projects/{self.project.pk}/labels/': {'name': 'x', 'key': 'x'},
            f'/projects/{self.project.pk}/releases/': {'name': 'x'},
            f'/tasks/{self.task.pk}/save/': {'revision': 0, 'annotation': annotation},
            f'/tasks/{self.task.pk}/assign/': {'user': self.outsider.pk, 'revision': 0},
            f'/tasks/{self.task.pk}/review/': {'decision': 'approved', 'revision': 0},
            f'/tasks/{self.task.pk}/comment/': {'note': 'x'},
            f'/tasks/{self.task.pk}/restore/': {'revision': 0, 'event_id': 1},
            f'/projects/{self.project.pk}/assign/': {'user': self.outsider.pk, 'task_ids': [self.task.pk]},
            f'/projects/{self.project.pk}/distribute/': {'user_ids': [self.outsider.pk], 'scope': 'all'},
            f'/projects/{self.project.pk}/geometry/': {'geometry': 'box'},
            f'/projects/{self.project.pk}/labels/{self.label.pk}/archive/': {},
            f'/projects/{self.project.pk}/tokens/': {'name': 'x'},
            f'/projects/{self.project.pk}/tokens/{self.release.pk}/revoke/': {},
            f'/projects/{self.project.pk}/webhooks/': {'url': 'https://x.test'},
            f'/projects/{self.project.pk}/webhooks/{self.release.pk}/delete/': {},
            f'/projects/{self.project.pk}/webhooks/{self.release.pk}/ping/': {},
            '/projects/create/': {'workspace': self.workspace.pk, 'name': 'pwned'},
        }
        for path, data in posts.items():
            self.assertEqual(self.client.post(path, json.dumps(data), content_type='application/json').status_code, 404, path)
        self.assertEqual(Membership.objects.filter(workspace=self.workspace).count(), 1)
        self.assertEqual(self.project.labels.count(), 1)
        self.assertEqual(self.project.releases.count(), 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.revision, 0)
        self.assertIsNone(self.task.assignee)
        self.assertEqual(Project.objects.filter(workspace=self.workspace).count(), 1)

    def test_upload_to_foreign_project_rejected(self):
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(buffer, 'PNG')
        result = self.client.post(f'/projects/{self.project.pk}/upload/', {'images': SimpleUploadedFile('x.png', buffer.getvalue(), content_type='image/png')})
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.project.tasks.count(), 1)

    def test_home_does_not_leak_foreign_projects(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret project')
        self.assertNotContains(response, 'Lab A')

    def test_project_page_not_in_foreign_querysets(self):
        self.assertFalse(Project.objects.filter(workspace__membership__user=self.outsider, pk=self.project.pk).exists())
        self.assertFalse(Task.objects.filter(project__workspace__membership__user=self.outsider).exists())

    def test_superuser_has_owner_access_everywhere(self):
        admin = get_user_model().objects.create_superuser('root', password='Test-root-923!')
        self.client.force_login(admin)
        for path in [f'/projects/{self.project.pk}/', f'/tasks/{self.task.pk}/', f'/releases/{self.release.pk}/', f'/releases/{self.release.pk}/coco/']:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        asset = self.client.get(f'/tasks/{self.task.pk}/asset/')
        self.assertEqual(asset.status_code, 200)
        close_response(asset)
        annotation = {'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': {}}], 'relations': []}
        response = self.client.post(f'/tasks/{self.task.pk}/save/', json.dumps({'revision': 0, 'annotation': annotation}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get('/'), 'Secret project')

    def test_unauthenticated_redirects_everywhere(self):
        self.client.logout()
        for path in ['/', f'/projects/{self.project.pk}/', f'/tasks/{self.task.pk}/', f'/tasks/{self.task.pk}/asset/', f'/releases/{self.release.pk}/']:
            self.assertEqual(self.client.get(path).status_code, 302, path)


class ConcurrencyTests(TransactionTestCase):
    """Phase 5: concurrent workers must not corrupt or double-decide work.
    Uses TransactionTestCase so writes truly commit and threads race for real."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.override.disable)
        User = get_user_model()
        self.owner = User.objects.create_user('owner', password='Test-owner-923!')
        self.worker = User.objects.create_user('worker', password='Test-worker-923!')
        self.reviewer = User.objects.create_user('reviewer', password='Test-reviewer-923!')
        self.workspace = Workspace.objects.create(name='Lab')
        for user, role in [(self.owner, 'owner'), (self.worker, 'annotator'), (self.reviewer, 'reviewer')]:
            Membership.objects.create(workspace=self.workspace, user=user, role=role)
        self.project = Project.objects.create(workspace=self.workspace, name='Race')
        self.label = Label.objects.create(project=self.project, name='Switch', key='switch', attributes=[])

    def test_concurrent_reviews_cannot_double_decide(self):
        buffer = io.BytesIO()
        Image.new('RGB', (60, 40), 'red').save(buffer, 'PNG')
        task = Task.objects.create(project=self.project, filename='t.png', digest='race1', width=60, height=40, image='x.jpg',
                                   state='submitted', assignee=self.worker, revision=1,
                                   annotation={'regions': [{'id': 'a', 'label': str(self.label.pk), 'type': 'point', 'points': [[.5, .5]], 'attributes': {}}], 'relations': []})
        session = Client()
        session.force_login(self.reviewer)
        cookie = session.cookies[settings.SESSION_COOKIE_NAME].value
        results = []

        def decide(_):
            try:
                c = Client()
                c.cookies[settings.SESSION_COOKIE_NAME] = cookie
                body = json.dumps({'decision': 'approved', 'revision': 1})
                results.append(c.post(f'/tasks/{task.pk}/review/', body, content_type='application/json').status_code)
            finally:
                close_old_connections()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(decide, range(8)))
        self.assertEqual(results.count(200), 1, results)
        task.refresh_from_db()
        self.assertEqual(task.state, 'approved')
        self.assertEqual(Event.objects.filter(task=task, action='review.approved').count(), 1)

    def test_crashed_worker_job_is_reclaimed(self):
        from studio.jobs import HANDLERS
        HANDLERS['test.noop'] = lambda job: {'ok': True}
        self.addCleanup(HANDLERS.pop, 'test.noop')
        job = Job.objects.create(type='test.noop', status='running', attempts=1, run_after=timezone.now())
        Job.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(run_pending(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')

    def test_backup_and_restore_roundtrip(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        call_command('backupdata', out=out)
        archive = os.path.join(out, os.listdir(out)[0])
        Task.objects.create(project=self.project, filename='t.png', digest='d1', width=10, height=10, image='x.jpg')
        scratch_path = os.path.join(out, 'restored.sqlite3')
        call_command('restoredata', archive, db=scratch_path)
        scratch = sqlite3.connect(scratch_path)
        try:
            name = scratch.execute('SELECT name FROM studio_project WHERE id=?', (self.project.pk,)).fetchone()[0]
            self.assertEqual(name, 'Race')
            self.assertIsNone(scratch.execute('SELECT id FROM studio_task WHERE filename=?', ('t.png',)).fetchone())
        finally:
            scratch.close()
