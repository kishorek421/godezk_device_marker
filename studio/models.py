from django.db import models
from django.conf import settings
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
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    instructions = models.TextField(blank=True)
    schema_version = models.PositiveIntegerField(default=1)
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


class Event(models.Model):
    project = models.ForeignKey(Project, related_name='events', on_delete=models.CASCADE)
    task = models.ForeignKey(Task, null=True, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Release(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, related_name='releases', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    snapshot = models.JSONField()
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
