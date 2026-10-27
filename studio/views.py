from django.shortcuts import render, get_object_or_404, redirect

# Create your views here.

import hashlib
import io
import json
import math
import re
from functools import wraps
from PIL import Image, UnidentifiedImageError
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction, IntegrityError
from django.http import JsonResponse, FileResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from .models import Workspace, Membership, Project, Label, Task, Event, Release


class Invalid(Exception):
    pass


def api(fn):
    @login_required
    @require_POST
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        try:
            if request.content_type == 'application/json':
                request.data = json.loads(request.body)
                if not isinstance(request.data, dict):
                    raise Invalid('Expected an object')
            else:
                request.data = request.POST
            return fn(request, *args, **kwargs)
        except (Invalid, ValidationError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, KeyError):
            return JsonResponse({'error': 'Invalid request data'}, status=400)
        except IntegrityError:
            return JsonResponse({'error': 'This key, username, or asset already exists.'}, status=409)
    return wrapped


def member(request, workspace_id, roles=None):
    m = get_object_or_404(Membership, user=request.user, workspace_id=workspace_id)
    if roles and m.role not in roles:
        raise Invalid('Your role cannot perform this action')
    return m


def project_access(request, pk, roles=None):
    p = get_object_or_404(Project, pk=pk, workspace__membership__user=request.user)
    return p, member(request, p.workspace_id, roles)


def task_access(request, pk):
    t = get_object_or_404(Task.objects.select_related('project'), pk=pk, project__workspace__membership__user=request.user)
    return t, member(request, t.project.workspace_id)


def text(data, key, maximum=160):
    value = data.get(key, '')
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Invalid(f'{key} is required (maximum {maximum} characters)')
    return value.strip()


def event(request, project, action, task=None, payload=None):
    Event.objects.create(project=project, task=task, actor=request.user, action=action, payload=payload or {})


@login_required
def home(request):
    memberships = Membership.objects.filter(user=request.user).select_related('workspace')
    projects = Project.objects.filter(workspace__membership__user=request.user).select_related('workspace').order_by('-created_at')
    return render(request, 'studio/home.html', {'memberships': memberships, 'projects': projects})


@api
@transaction.atomic
def workspace_create(request):
    w = Workspace.objects.create(name=text(request.data, 'name', 120))
    Membership.objects.create(workspace=w, user=request.user, role='owner')
    return JsonResponse({'redirect': '/'})


@api
@transaction.atomic
def project_create(request):
    m = member(request, request.data['workspace'], ['owner'])
    p = Project.objects.create(workspace=m.workspace, name=text(request.data, 'name'), instructions=request.data.get('instructions', '')[:10000])
    event(request, p, 'project.created')
    return JsonResponse({'redirect': f'/projects/{p.pk}/'})


@login_required
def project_page(request, pk):
    p, m = project_access(request, pk)
    tasks = p.tasks.select_related('assignee').order_by('id')
    state = request.GET.get('state')
    if state:
        tasks = tasks.filter(state=state)
    query = request.GET.get('q', '')[:100]
    if query:
        tasks = tasks.filter(filename__icontains=query)
    return render(request, 'studio/project.html', {'project': p, 'membership': m, 'tasks': tasks[:200], 'labels': p.labels.all(), 'members': Membership.objects.filter(workspace=p.workspace).select_related('user'), 'events': p.events.select_related('actor').order_by('-id')[:30], 'releases': p.releases.order_by('-created_at'), 'states': Task.STATES})


@api
@transaction.atomic
def member_add(request, pk):
    p, _ = project_access(request, pk, ['owner'])
    role = request.data.get('role')
    if role not in ['annotator', 'reviewer']:
        raise Invalid('Choose annotator or reviewer')
    username = text(request.data, 'username', 150)
    user = get_user_model().objects.filter(username=username).first()
    if not user:
        user = get_user_model()(username=username)
        password = text(request.data, 'password', 256)
        validate_password(password, user)
        user.set_password(password)
        user.save()
    Membership.objects.create(workspace=p.workspace, user=user, role=role)
    event(request, p, 'member.added', payload={'username': username, 'role': role})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def label_create(request, pk):
    p, _ = project_access(request, pk, ['owner'])
    key = text(request.data, 'key', 64)
    color = request.data.get('color', '#8b5cf6')
    if not re.fullmatch(r'[-a-zA-Z0-9_]+', key) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise Invalid('Use a slug key and a hexadecimal color')
    raw = request.data.get('attributes', '')
    attributes = [s.strip() for s in raw.split(',') if s.strip()]
    if len(attributes) > 20 or any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,39}', a) for a in attributes) or len(set(attributes)) != len(attributes):
        raise Invalid('Attributes must be unique identifiers (up to 20)')
    Label.objects.create(project=p, key=key, name=text(request.data, 'name', 80), color=color, attributes=attributes)
    p.schema_version += 1
    p.save(update_fields=['schema_version'])
    event(request, p, 'schema.label_created', payload={'key': key, 'version': p.schema_version})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def upload(request, pk):
    p, _ = project_access(request, pk, ['owner', 'reviewer'])
    files = request.FILES.getlist('images')
    if not files or len(files) > 30:
        raise Invalid('Upload between 1 and 30 images')
    prepared = []
    for f in files:
        if f.size > 20 * 1024 * 1024:
            raise Invalid('Each image must be smaller than 20 MB')
        raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ['PNG', 'JPEG', 'WEBP'] or image.width * image.height > 40_000_000:
                    raise Invalid('Use PNG, JPEG, or WebP images under 40 megapixels')
                image.load()
                width, height = image.size
                output = io.BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=95)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise Invalid('Invalid or unsafe image file')
        if p.tasks.filter(digest=digest).exists() or digest in [item[0] for item in prepared]:
            raise Invalid(f'Duplicate image: {f.name}')
        prepared.append((digest, f.name[:255], width, height, output.getvalue()))
    for digest, name, width, height, content in prepared:
        task = Task(project=p, digest=digest, filename=name, width=width, height=height)
        task.image.save(f'{digest}.jpg', ContentFile(content), save=True)
        event(request, p, 'task.uploaded', task)
    return JsonResponse({'reload': True, 'count': len(prepared)})


@login_required
def asset(request, pk):
    task, _ = task_access(request, pk)
    response = FileResponse(task.image.open('rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
def editor(request, pk):
    t, m = task_access(request, pk)
    labels = list(t.project.labels.filter(archived=False).values('id', 'key', 'name', 'color', 'attributes'))
    editable = t.state in ['open', 'in_progress', 'changes_requested'] and (t.assignee_id == request.user.pk or m.role == 'owner')
    data = {'id': t.pk, 'revision': t.revision, 'annotation': t.annotation, 'labels': labels, 'editable': editable, 'state': t.state}
    return render(request, 'studio/editor.html', {'task': t, 'membership': m, 'editor_data': data, 'editable': editable, 'events': t.event_set.select_related('actor').order_by('-id')[:20]})


def validate_annotation(value, project):
    if not isinstance(value, dict) or set(value) - {'regions', 'relations'}:
        raise Invalid('Invalid annotation document')
    regions, relations = value.get('regions', []), value.get('relations', [])
    if not isinstance(regions, list) or not isinstance(relations, list) or len(regions) > 1000 or len(relations) > 2000:
        raise Invalid('Invalid annotation limits')
    labels = {str(l.pk): l for l in project.labels.filter(archived=False)}
    ids = set()
    for r in regions:
        if not isinstance(r, dict) or set(r) - {'id', 'label', 'type', 'points', 'attributes'}:
            raise Invalid('Invalid region')
        rid = r.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', rid) or rid in ids:
            raise Invalid('Region IDs must be unique')
        ids.add(rid)
        label = labels.get(r.get('label'))
        if not label:
            raise Invalid('Unknown label')
        points = r.get('points')
        kind = r.get('type')
        if kind not in ['box', 'polygon', 'point'] or not isinstance(points, list):
            raise Invalid('Invalid geometry type')
        if (kind == 'box' and len(points) != 2) or (kind == 'polygon' and not 3 <= len(points) <= 500) or (kind == 'point' and len(points) != 1):
            raise Invalid('Invalid geometry')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(type(n) not in [int, float] or not math.isfinite(n) or not 0 <= n <= 1 for n in point):
                raise Invalid('Coordinates must be finite and normalized between 0 and 1')
        if kind == 'box' and (points[0][0] >= points[1][0] or points[0][1] >= points[1][1]):
            raise Invalid('Box must have positive area')
        attrs = r.get('attributes', {})
        if not isinstance(attrs, dict) or set(attrs) - set(label.attributes) or any(not isinstance(v, str) or len(v) > 1000 for v in attrs.values()):
            raise Invalid('Invalid label attributes')
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {'from', 'to', 'key'} or relation['from'] not in ids or relation['to'] not in ids or relation['from'] == relation['to']:
            raise Invalid('Relationships must connect two existing, distinct regions')
        if not isinstance(relation['key'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', relation['key']):
            raise Invalid('Invalid relationship key')
    return {'regions': regions, 'relations': relations}


@api
@transaction.atomic
def save(request, pk):
    t, m = task_access(request, pk)
    if t.state not in ['open', 'in_progress', 'changes_requested'] or (t.assignee_id != request.user.pk and m.role != 'owner'):
        return JsonResponse({'error': 'Task is not editable by you'}, status=403)
    annotation = validate_annotation(request.data.get('annotation'), t.project)
    submit = request.data.get('submit') is True
    revision = request.data.get('revision')
    if type(revision) is not int:
        raise Invalid('Revision is required')
    changed = Task.objects.filter(pk=t.pk, revision=revision, state=t.state).update(annotation=annotation, revision=revision + 1, state='submitted' if submit else 'in_progress', assignee=request.user)
    if not changed:
        return JsonResponse({'error': 'Conflict: a newer revision exists. Reload before saving.'}, status=409)
    event(request, t.project, 'task.submitted' if submit else 'annotation.saved', t, {'revision': revision + 1, 'schema_version': t.project.schema_version, 'annotation': annotation})
    return JsonResponse({'revision': revision + 1, 'submitted': submit})


@api
@transaction.atomic
def assign(request, pk):
    t, m = task_access(request, pk)
    if m.role not in ['owner', 'reviewer']:
        return JsonResponse({'error': 'Only managers can assign tasks'}, status=403)
    target = get_object_or_404(Membership, workspace=t.project.workspace, user_id=request.data['user'])
    if t.state in ['submitted', 'approved']:
        raise Invalid('Submitted and approved tasks cannot be reassigned')
    revision = request.data.get('revision')
    if type(revision) is not int:
        raise Invalid('Revision is required')
    if not Task.objects.filter(pk=t.pk, revision=revision).update(assignee=target.user, revision=revision + 1):
        return JsonResponse({'error': 'Task changed. Reload and retry.'}, status=409)
    event(request, t.project, 'task.assigned', t, {'user': target.user.username})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def review(request, pk):
    t, m = task_access(request, pk)
    if m.role not in ['owner', 'reviewer'] or t.assignee_id == request.user.pk:
        return JsonResponse({'error': 'A different reviewer must review this task'}, status=403)
    decision = request.data.get('decision')
    if decision not in ['approved', 'changes_requested']:
        raise Invalid('Invalid decision')
    note = request.data.get('note', '')
    if not isinstance(note, str) or len(note) > 4000 or (decision == 'changes_requested' and not note.strip()):
        raise Invalid('Changes requested requires a review note (maximum 4000 characters)')
    revision = request.data.get('revision')
    if type(revision) is not int:
        raise Invalid('Revision is required')
    if not Task.objects.filter(pk=t.pk, revision=revision, state='submitted').update(state=decision, revision=revision + 1):
        return JsonResponse({'error': 'Task changed or is not submitted. Reload.'}, status=409)
    event(request, t.project, f'review.{decision}', t, {'note': note, 'revision': revision + 1})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def release_create(request, pk):
    p, _ = project_access(request, pk, ['owner', 'reviewer'])
    tasks = list(p.tasks.filter(state='approved').values('id', 'filename', 'digest', 'width', 'height', 'revision', 'annotation'))
    if not tasks:
        raise Invalid('At least one approved task is required')
    snapshot = {'format': 'annotation-studio/v1', 'project': p.name, 'schema_version': p.schema_version, 'labels': list(p.labels.values('id', 'key', 'name', 'color', 'attributes')), 'tasks': tasks, 'provenance': list(p.events.filter(task_id__in=[t['id'] for t in tasks]).values('task_id', 'actor__username', 'action', 'payload', 'created_at'))}
    snapshot = json.loads(json.dumps(snapshot, default=str))
    release = Release.objects.create(project=p, name=text(request.data, 'name', 120), snapshot=snapshot, creator=request.user)
    event(request, p, 'release.created', payload={'id': str(release.pk)})
    return JsonResponse({'reload': True})


@login_required
def release_download(request, pk):
    release = get_object_or_404(Release, pk=pk, project__workspace__membership__user=request.user)
    response = JsonResponse(release.snapshot, json_dumps_params={'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="release-{release.pk}.json"'
    response['Cache-Control'] = 'private, no-store'
    return response
