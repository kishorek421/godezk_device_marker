from django.test import TestCase

# Create your tests here.

import io
import json
import tempfile
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings, Client
from .models import Workspace, Membership, Project, Label, Task, Release, Event


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
            {'id': 'b', 'label': str(self.label.pk), 'type': 'point', 'points': [[.8, .7]], 'attributes': {}}
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
        doc = self.annotation(); doc['regions'][0]['type'] = 'polygon'; doc['regions'][0]['points'] = [[0, 0], [1, 0], [.5, 1]]
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': doc}).status_code, 200)

    def test_review_rejection_and_resubmission(self):
        task = self.upload()
        self.post(f'/tasks/{task.pk}/save/', {'revision': 0, 'annotation': self.annotation(), 'submit': True})
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'approved'}).status_code, 403)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'changes_requested'}).status_code, 400)
        self.assertEqual(self.post(f'/tasks/{task.pk}/review/', {'revision': 1, 'decision': 'changes_requested', 'note': 'Tighten box'}).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(f'/tasks/{task.pk}/save/', {'revision': 2, 'annotation': self.annotation(), 'submit': True}).status_code, 200)

    def test_upload_validation_and_duplicate_detection(self):
        self.upload()
        self.assertEqual(self.client.post(f'/projects/{self.project.pk}/upload/', {'images': self.image()}).status_code, 400)
        bad = SimpleUploadedFile('fake.png', b'<script>bad</script>', content_type='image/png')
        self.assertEqual(self.client.post(f'/projects/{self.project.pk}/upload/', {'images': bad}).status_code, 400)
        self.assertEqual(self.project.tasks.count(), 1)

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
        response.close()

    def test_empty_release_and_bad_json(self):
        self.assertEqual(self.post(f'/projects/{self.project.pk}/releases/', {'name': 'empty'}).status_code, 400)
        self.assertEqual(self.client.post('/workspaces/create/', '{bad', content_type='application/json').status_code, 400)
