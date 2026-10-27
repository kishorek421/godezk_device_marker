import io
import json
import tempfile
from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect
from .models import Workspace, Membership, Task, Release


class BrowserWorkflowTests(StaticLiveServerTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.override.disable)
        User = get_user_model()
        self.owner = User.objects.create_user('manager', password='Browser-manager-482!')
        self.worker = User.objects.create_user('artist', password='Browser-artist-482!')
        self.reviewer = User.objects.create_user('quality', password='Browser-quality-482!')
        workspace = Workspace.objects.create(name='Browser Lab')
        for user, role in [(self.owner, 'owner'), (self.worker, 'annotator'), (self.reviewer, 'reviewer')]:
            Membership.objects.create(workspace=workspace, user=user, role=role)
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1100})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)

    def login(self, username, password):
        self.page.goto(self.live_server_url + '/accounts/login/')
        self.page.get_by_label('Username', exact=True).fill(username)
        self.page.get_by_label('Password', exact=True).fill(password)
        self.page.get_by_role('button', name='Sign In').click()
        self.page.wait_for_url(self.live_server_url + '/')

    def test_complete_image_annotation_and_review_in_browser(self):
        p = self.page
        self.login('manager', 'Browser-manager-482!')
        p.get_by_label('Project name', exact=True).fill('Device labeling')
        p.get_by_label('Annotation instructions').fill('Mark switches and link their controls.')
        p.get_by_role('button', name='Create project').click()
        p.wait_for_url('**/projects/*/')
        project_url = p.url
        p.get_by_text('Add label key', exact=True).click()
        p.get_by_label('Display name').fill('Switch')
        p.get_by_label('Stable key').fill('switch')
        p.get_by_label('Text attributes, comma-separated').fill('state')
        with p.expect_navigation():
            p.get_by_role('button', name='Add label', exact=True).click()
        expect(p.get_by_text('switch · state', exact=True)).to_be_visible()
        output = io.BytesIO()
        Image.new('RGB', (900, 600), '#d8e0ec').save(output, 'PNG')
        p.locator('input[type=file]').set_input_files({'name': 'switch-panel.png', 'mimeType': 'image/png', 'buffer': output.getvalue()})
        with p.expect_navigation():
            p.get_by_role('button', name='Upload to dataset').click()
        p.locator('.assign select').select_option(str(self.worker.pk))
        with p.expect_navigation():
            p.get_by_role('button', name='Assign', exact=True).click()
        task_id = int(p.get_by_role('link', name='switch-panel.png', exact=True).get_attribute('href').strip('/').split('/')[-1])
        p.get_by_role('button', name='Sign out').click()
        self.login('artist', 'Browser-artist-482!')
        p.goto(self.live_server_url + f'/tasks/{task_id}/')
        p.locator('#source-image').wait_for()
        p.wait_for_function('document.getElementById("source-image").naturalWidth > 0')
        bounds = p.locator('#overlay').bounding_box()
        def point(x, y):
            return bounds['x'] + bounds['width'] * x, bounds['y'] + bounds['height'] * y
        p.get_by_role('button', name='Box (B)', exact=True).click()
        p.mouse.move(*point(.12, .15)); p.mouse.down(); p.mouse.move(*point(.42, .55), steps=8); p.mouse.up()
        expect(p.locator('#region-count')).to_have_text('1')
        p.locator('#attributes input').fill('on')
        p.locator('#attributes input').press('Tab')
        p.get_by_role('button', name='Polygon (P)', exact=True).click()
        for coordinates in [(.6, .2), (.85, .2), (.75, .5)]:
            p.mouse.click(*point(*coordinates))
        p.keyboard.press('Enter')
        expect(p.locator('#region-count')).to_have_text('2')
        p.get_by_role('button', name='Point (K)', exact=True).click()
        p.mouse.click(*point(.8, .8))
        expect(p.locator('#region-count')).to_have_text('3')
        p.get_by_role('button', name='Undo', exact=True).click()
        expect(p.locator('#region-count')).to_have_text('2')
        p.get_by_role('button', name='Redo', exact=True).click()
        expect(p.locator('#region-count')).to_have_text('3')
        values = p.locator('#rel-from option').evaluate_all('(nodes) => nodes.map(n => n.value)')
        p.locator('#rel-from').select_option(values[0]); p.locator('#rel-to').select_option(values[1])
        p.get_by_label('Relationship key', exact=True).fill('controls')
        p.get_by_role('button', name='Link regions').click()
        expect(p.locator('#relations')).to_contain_text('controls')
        with p.expect_response(lambda response: response.url.endswith('/save/') and response.status == 200):
            p.get_by_role('button', name='Save', exact=True).click()
        expect(p.locator('#save-status')).to_contain_text('Saved · revision')
        p.reload()
        expect(p.locator('#region-count')).to_have_text('3')
        expect(p.locator('#relations')).to_contain_text('controls')
        saved = json.loads(p.locator('#editor-data').text_content())
        self.assertEqual(saved['annotation']['regions'][0]['attributes']['state'], 'on')
        with p.expect_navigation():
            p.get_by_role('button', name='Submit for review').click()
        expect(p.locator('.status')).to_have_text('Submitted')
        expect(p.get_by_role('button', name='Save', exact=True)).to_have_count(0)
        p.get_by_role('button', name='Sign out').click()
        self.login('quality', 'Browser-quality-482!')
        p.goto(self.live_server_url + f'/tasks/{task_id}/')
        p.get_by_label('Feedback').fill('Geometry and relation verified.')
        with p.expect_navigation():
            p.get_by_role('button', name='Record review').click()
        expect(p.locator('.status')).to_have_text('Approved')
        p.goto(project_url)
        p.get_by_label('Release name').fill('verified-v1')
        with p.expect_navigation():
            p.get_by_role('button', name='Create approved snapshot').click()
        with p.expect_download() as download:
            p.get_by_role('link', name='verified-v1').click()
        self.assertTrue(download.value.suggested_filename.endswith('.json'))
        release_path = p.get_by_role('link', name='verified-v1').get_attribute('href')
        snapshot = p.request.get(self.live_server_url + release_path).json()
        self.assertEqual(len(snapshot['tasks'][0]['annotation']['regions']), 3)
        self.assertEqual(snapshot['tasks'][0]['annotation']['relations'][0]['key'], 'controls')
        p.set_viewport_size({'width': 390, 'height': 844})
        expect(p.get_by_role('heading', name='Device labeling', exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])
