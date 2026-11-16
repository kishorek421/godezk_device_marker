from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid

# Create your models here.


class Workspace(models.Model):
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)


class Membership(models.Model):
    ROLES = [('owner', 'Owner'), ('reviewer', 'Reviewer'), ('annotator', 'Annotator')]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=ROLES)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'user'], name='unique_member')]


class Project(models.Model):
    GEOMETRIES = [('any', 'Any'), ('box', 'Box'), ('polygon', 'Polygon'), ('point', 'Point')]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    instructions = models.TextField(blank=True)
    schema_version = models.PositiveIntegerField(default=1)
    geometry = models.CharField(max_length=8, choices=GEOMETRIES, default='any')
    agent_predictor = models.CharField(max_length=40, blank=True)
    agent_config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Label(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, related_name='labels', on_delete=models.CASCADE)
    key = models.SlugField(max_length=64)
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default='#206e69')
    attributes = models.JSONField(default=list)
    archived = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'key'], name='unique_project_key')]

    def attribute_defs(self):
        return [{'key': a, 'options': None} if isinstance(a, str) else a for a in self.attributes or []]

    @property
    def attributes_display(self):
        return ', '.join(d['key'] + (f"({'|'.join(d['options'])})" if d['options'] else '') for d in self.attribute_defs())


class Task(models.Model):
    STATES = [('open', 'Open'), ('in_progress', 'In progress'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('changes_requested', 'Changes requested')]
    project = models.ForeignKey(Project, related_name='tasks', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='images/%Y/%m/')
    filename = models.CharField(max_length=255)
    digest = models.CharField(max_length=64)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    state = models.CharField(max_length=24, choices=STATES, default='open')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    revision = models.PositiveIntegerField(default=0)
    annotation = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'digest'], name='unique_project_asset')]
        indexes = [models.Index(fields=['project', 'state']), models.Index(fields=['project', 'assignee'])]


class Event(models.Model):
    project = models.ForeignKey(Project, related_name='events', on_delete=models.CASCADE)
    task = models.ForeignKey(Task, null=True, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['project', 'created_at']), models.Index(fields=['project', 'action'])]


class Release(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, related_name='releases', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    snapshot = models.JSONField()
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class ApiToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, related_name='api_tokens', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True)


class Webhook(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, related_name='webhooks', on_delete=models.CASCADE)
    url = models.CharField(max_length=500)
    secret = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook = models.ForeignKey(Webhook, related_name='deliveries', on_delete=models.CASCADE)
    action = models.CharField(max_length=40)
    status = models.CharField(max_length=12)
    response_code = models.PositiveIntegerField(null=True)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Job(models.Model):
    STATUSES = [('pending', 'Pending'), ('running', 'Running'), ('done', 'Done'), ('dead', 'Dead')]
    type = models.CharField(max_length=40)
    status = models.CharField(max_length=12, choices=STATUSES, default='pending', db_index=True)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    error = models.CharField(max_length=500, blank=True)
    run_after = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'run_after'])]
