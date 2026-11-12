from django.shortcuts import render, get_object_or_404, redirect

# Create your views here.

import hashlib
import hmac
import io
import json
import math
import re
import secrets
import tempfile
import time
import urllib.request
import zipfile
from datetime import timedelta
from functools import wraps
from PIL import Image, UnidentifiedImageError
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.utils import OperationalError
from django.db.models import Count
from django.http import JsonResponse, FileResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Workspace, Membership, Project, Label, Task, Event, Release, ApiToken, Webhook, WebhookDelivery, Job
from .jobs import enqueue, post_signed


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
        except (Invalid, ValidationError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, KeyError):
            return JsonResponse({'error': 'Invalid request data'}, status=400)
        for attempt in range(4):
            try:
                return fn(request, *args, **kwargs)
            except (Invalid, ValidationError) as exc:
                return JsonResponse({'error': str(exc)}, status=400)
            except IntegrityError:
                return JsonResponse({'error': 'This key, username, or asset already exists.'}, status=409)
            except OperationalError as exc:
                if 'locked' not in str(exc) or attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
    return wrapped


def member(request, workspace_id, roles=None):
    if request.user.is_superuser:
        return Membership(workspace_id=workspace_id, user=request.user, role='owner')
    m = get_object_or_404(Membership, user=request.user, workspace_id=workspace_id)
    if roles and m.role not in roles:
        raise Invalid('Your role cannot perform this action')
    return m


def project_access(request, pk, roles=None):
    if request.user.is_superuser:
        p = get_object_or_404(Project, pk=pk)
    else:
        p = get_object_or_404(Project, pk=pk, workspace__membership__user=request.user)
    return p, member(request, p.workspace_id, roles)


def task_access(request, pk):
    if request.user.is_superuser:
        t = get_object_or_404(Task.objects.select_related('project'), pk=pk)
    else:
        t = get_object_or_404(Task.objects.select_related('project'), pk=pk, project__workspace__membership__user=request.user)
    return t, member(request, t.project.workspace_id)


def text(data, key, maximum=160):
    value = data.get(key, '')
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Invalid(f'{key} is required (maximum {maximum} characters)')
    return value.strip()


def event(request, project, action, task=None, payload=None):
    Event.objects.create(project=project, task=task, actor=request.user, action=action, payload=payload or {})
    safe = {k: v for k, v in (payload or {}).items() if k != 'annotation'}
    body = {'event': action, 'project_id': project.pk, 'task_id': task.pk if task else None,
            'actor': request.user.username, 'payload': safe, 'timestamp': timezone.now().isoformat()}
    for hook in project.webhooks.filter(active=True):
        transaction.on_commit(lambda hook=hook, body=body: deliver(hook, body))


def deliver(hook, body):
    delivery = {'webhook': hook, 'action': body['event'], 'status': 'failed', 'response_code': None, 'error': ''}
    try:
        code = post_signed(hook.url, hook.secret, body)
        delivery['response_code'] = code
        delivery['status'] = 'delivered' if 200 <= code < 300 else 'failed'
    except Exception as exc:
        delivery['error'] = str(exc)[:300]
    d = WebhookDelivery.objects.create(**delivery)
    if d.status != 'delivered':
        enqueue('webhook.deliver', {'delivery_id': str(d.pk), 'body': body})


def health(request):
    try:
        Workspace.objects.exists()
    except Exception:
        return JsonResponse({'status': 'error', 'db': 'unreachable'}, status=503)
    data = {'status': 'ok', 'db': 'ok'}
    if request.user.is_superuser:
        data.update({
            'projects': Project.objects.count(),
            'tasks': Task.objects.count(),
            'jobs_pending': Job.objects.filter(status='pending').count(),
            'jobs_dead': Job.objects.filter(status='dead').count(),
            'deliveries_failed': WebhookDelivery.objects.filter(status='failed').count(),
        })
    return JsonResponse(data)


@login_required
def home(request):
    if request.user.is_superuser:
        memberships = [Membership(workspace=w, user=request.user, role='owner') for w in Workspace.objects.all()]
        projects = Project.objects.select_related('workspace').order_by('-created_at')
    else:
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
    members = list(Membership.objects.filter(workspace=p.workspace).select_related('user'))
    tasks = p.tasks.select_related('assignee').order_by('id')
    state = request.GET.get('state')
    if state:
        tasks = tasks.filter(state=state)
    query = request.GET.get('q', '')[:100]
    if query:
        tasks = tasks.filter(filename__icontains=query)
    page = Paginator(tasks, 50).get_page(request.GET.get('page'))
    received = {}
    for row in p.events.filter(action__in=['review.approved', 'review.changes_requested']).values('task__assignee__username', 'action').annotate(n=Count('id')):
        received.setdefault(row['task__assignee__username'], {'approved': 0, 'changes_requested': 0})[row['action'].split('.')[1]] += row['n']
    rows = []
    for mem in members:
        counts = dict(p.tasks.filter(assignee=mem.user).values_list('state').annotate(n=Count('id')).values_list('state', 'n'))
        if not counts:
            continue
        got = received.get(mem.user.username, {'approved': 0, 'changes_requested': 0})
        decided = got['approved'] + got['changes_requested']
        rows.append({'username': mem.user.username, 'role': mem.get_role_display(),
                     'open': counts.get('open', 0), 'in_progress': counts.get('in_progress', 0), 'submitted': counts.get('submitted', 0),
                     'approved': counts.get('approved', 0), 'changes_requested': counts.get('changes_requested', 0),
                     'rate': f"{round(100 * got['approved'] / decided)}%" if decided else '—'})
    given_map = {}
    for row in p.events.filter(action__in=['review.approved', 'review.changes_requested']).values('actor__username', 'action').annotate(n=Count('id')):
        given_map.setdefault(row['actor__username'], {'approved': 0, 'changes_requested': 0})[row['action'].split('.')[1]] += row['n']
    given = [{'username': u, 'approved': v['approved'], 'changes': v['changes_requested'], 'total': v['approved'] + v['changes_requested']}
             for u, v in given_map.items()]
    progress = {'rows': rows, 'given': given, 'total': p.tasks.count(), 'approved': p.tasks.filter(state='approved').count(),
                'submitted_7d': p.events.filter(action='task.submitted', created_at__gte=timezone.now() - timedelta(days=7)).count()}
    hooks = [(w, w.deliveries.order_by('-created_at').first()) for w in p.webhooks.all()]
    return render(request, 'studio/project.html', {'project': p, 'membership': m, 'tasks': page, 'labels': p.labels.all(), 'members': members, 'events': p.events.select_related('actor').order_by('-id')[:30], 'releases': p.releases.order_by('-created_at'), 'states': Task.STATES, 'progress': progress,
                                                   'api_tokens': p.workspace.api_tokens.filter(revoked=False).select_related('user'),
                                                   'webhooks': hooks})


@login_required
def review_next(request, pk):
    p, m = project_access(request, pk)
    if m.role not in ['owner', 'reviewer']:
        return HttpResponseForbidden('Only reviewers can use the review queue')
    nxt = p.tasks.filter(state='submitted').order_by('id').first()
    return redirect(f'/tasks/{nxt.pk}/' if nxt else f'/projects/{p.pk}/')


@login_required
def spot_check(request, pk):
    p, m = project_access(request, pk)
    if m.role not in ['owner', 'reviewer']:
        return HttpResponseForbidden('Only reviewers can spot-check approved work')
    t = p.tasks.filter(state='approved').order_by('?').first()
    if not t:
        return redirect(f'/projects/{p.pk}/')
    event(request, p, 'quality.sampled', t)
    return redirect(f'/tasks/{t.pk}/')


@api
@transaction.atomic
def geometry_set(request, pk):
    p, _ = project_access(request, pk, ['owner'])
    geometry = request.data.get('geometry')
    if geometry not in dict(Project.GEOMETRIES):
        raise Invalid('Choose a valid geometry lock')
    p.geometry = geometry
    p.save(update_fields=['geometry'])
    event(request, p, 'project.geometry_changed', payload={'geometry': geometry})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def label_archive(request, pk, label_id):
    p, _ = project_access(request, pk, ['owner'])
    label = get_object_or_404(Label, pk=label_id, project=p)
    label.archived = not label.archived
    label.save(update_fields=['archived'])
    p.schema_version += 1
    p.save(update_fields=['schema_version'])
    event(request, p, 'schema.label_archived' if label.archived else 'schema.label_restored',
          payload={'key': label.key, 'schema_version': p.schema_version})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def token_create(request, pk):
    p, _ = project_access(request, pk, ['owner'])
    name = text(request.data, 'name', 80)
    secret = 'gm_' + secrets.token_urlsafe(24)
    token = ApiToken.objects.create(workspace=p.workspace, user=request.user, name=name,
                                    prefix=secret[:12], key_hash=hashlib.sha256(secret.encode()).hexdigest())
    event(request, p, 'api.token_created', payload={'name': name})
    return JsonResponse({'token': secret, 'id': str(token.pk)})


@api
@transaction.atomic
def token_revoke(request, pk, token_id):
    p, _ = project_access(request, pk, ['owner'])
    token = get_object_or_404(ApiToken, pk=token_id, workspace=p.workspace)
    token.revoked = True
    token.save(update_fields=['revoked'])
    event(request, p, 'api.token_revoked', payload={'name': token.name})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def webhook_create(request, pk):
    p, _ = project_access(request, pk, ['owner'])
    url = request.data.get('url', '')
    if not isinstance(url, str) or not re.match(r'^https?://\S+$', url) or len(url) > 500:
        raise Invalid('Use a valid http(s) endpoint URL')
    if p.webhooks.count() >= 10:
        raise Invalid('Maximum 10 webhooks per project')
    Webhook.objects.create(project=p, url=url.strip(), secret=secrets.token_hex(24))
    event(request, p, 'webhook.created', payload={'url': url.strip()})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def webhook_delete(request, pk, hook_id):
    p, _ = project_access(request, pk, ['owner'])
    get_object_or_404(Webhook, pk=hook_id, project=p).delete()
    event(request, p, 'webhook.deleted')
    return JsonResponse({'reload': True})


@api
def webhook_ping(request, pk, hook_id):
    p, _ = project_access(request, pk, ['owner'])
    hook = get_object_or_404(Webhook, pk=hook_id, project=p)
    deliver(hook, {'event': 'webhook.ping', 'project_id': p.pk, 'task_id': None,
                   'actor': request.user.username, 'payload': {}, 'timestamp': timezone.now().isoformat()})
    last = hook.deliveries.order_by('-created_at').first()
    return JsonResponse({'reload': True, 'status': last.status})


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
    if not isinstance(raw, str):
        raise Invalid('Attributes must be a comma-separated string')
    attributes = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        attr_key, _, options = part.partition(':')
        attr_key = attr_key.strip()
        opts = [o.strip() for o in options.split('|') if o.strip()] if options else None
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,39}', attr_key):
            raise Invalid('Attributes must be identifiers')
        if opts and (len(opts) > 50 or len(set(opts)) != len(opts) or any(len(o) > 200 for o in opts)):
            raise Invalid('Attribute options must be unique values (up to 50, 200 characters each)')
        attributes.append({'key': attr_key, 'options': opts})
    if len(attributes) > 20 or len({a['key'] for a in attributes}) != len(attributes):
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
    assignee = None
    assign_to = request.data.get('assign_to')
    if assign_to:
        target = get_object_or_404(Membership, workspace=p.workspace, user_id=assign_to)
        if target.role != 'annotator':
            raise Invalid('Tasks can only be assigned to annotators')
        assignee = target.user
    prepared = []
    for f in files:
        if f.size > 20 * 1024 * 1024:
            raise Invalid('Each image must be smaller than 20 MB')
        raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ['PNG', 'JPEG', 'WEBP', 'BMP', 'GIF', 'TIFF'] or image.width * image.height > 40_000_000:
                    raise Invalid('Use PNG, JPEG, WebP, BMP, GIF, or TIFF images under 40 megapixels')
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
        task = Task(project=p, digest=digest, filename=name, width=width, height=height, assignee=assignee)
        task.image.save(f'{digest}.jpg', ContentFile(content), save=True)
        event(request, p, 'task.uploaded', task, payload={'assignee': assignee.username} if assignee else {})
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
    siblings = list(t.project.tasks.values_list('id', flat=True).order_by('id'))
    index = siblings.index(t.pk)
    nav = {'prev_id': siblings[index - 1] if index else None,
           'next_id': siblings[index + 1] if index + 1 < len(siblings) else None,
           'position': index + 1, 'total': len(siblings)}
    data = {'id': t.pk, 'revision': t.revision, 'annotation': t.annotation, 'labels': labels, 'editable': editable,
            'state': t.state, 'geometry': t.project.geometry, 'next_id': nav['next_id'], 'project_id': t.project_id}
    return render(request, 'studio/editor.html', {'task': t, 'membership': m, 'editor_data': data, 'editable': editable, 'nav': nav, 'events': t.event_set.select_related('actor').order_by('-id')[:20]})


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
        if project.geometry != 'any' and kind != project.geometry:
            raise Invalid(f'This project is locked to {project.geometry} annotations')
        if (kind == 'box' and len(points) != 2) or (kind == 'polygon' and not 3 <= len(points) <= 500) or (kind == 'point' and len(points) != 1):
            raise Invalid('Invalid geometry')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(type(n) not in [int, float] or not math.isfinite(n) or not 0 <= n <= 1 for n in point):
                raise Invalid('Coordinates must be finite and normalized between 0 and 1')
        if kind == 'box' and (points[0][0] >= points[1][0] or points[0][1] >= points[1][1]):
            raise Invalid('Box must have positive area')
        attrs = r.get('attributes', {})
        defs = {d['key']: d['options'] for d in label.attribute_defs()}
        if not isinstance(attrs, dict) or set(attrs) - set(defs) or any(not isinstance(v, str) or len(v) > 1000 for v in attrs.values()):
            raise Invalid('Invalid label attributes')
        if any(defs[k] is not None and v not in defs[k] for k, v in attrs.items()):
            raise Invalid('Attribute value is not an allowed option')
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
    if t.project.geometry == 'any':
        regions = request.data.get('annotation', {})
        if isinstance(regions, dict) and isinstance(regions.get('regions'), list) and regions['regions']:
            first = regions['regions'][0]
            if isinstance(first, dict) and first.get('type') in ['box', 'polygon', 'point']:
                t.project.geometry = first['type']
                t.project.save(update_fields=['geometry'])
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
def bulk_assign(request, pk):
    p, _ = project_access(request, pk, ['owner', 'reviewer'])
    target = get_object_or_404(Membership, workspace=p.workspace, user_id=request.data['user'])
    if target.role != 'annotator':
        raise Invalid('Tasks can only be assigned to annotators')
    ids = request.data.get('task_ids')
    if not isinstance(ids, list) or not 0 < len(ids) <= 200 or any(type(i) is not int for i in ids):
        raise Invalid('Choose between 1 and 200 tasks')
    tasks = p.tasks.filter(pk__in=ids)
    if tasks.count() != len(set(ids)):
        raise Invalid('Some selected tasks are not in this project')
    locked = tasks.filter(state__in=['submitted', 'approved']).count()
    if locked:
        raise Invalid(f'{locked} selected task(s) are submitted or approved and cannot be reassigned')
    for t in tasks:
        t.assignee = target.user
        t.revision += 1
        t.save(update_fields=['assignee', 'revision', 'updated_at'])
        event(request, p, 'task.assigned', t, {'user': target.user.username, 'bulk': True})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def distribute(request, pk):
    p, _ = project_access(request, pk, ['owner', 'reviewer'])
    ids = request.data.get('user_ids')
    if not isinstance(ids, list) or not 0 < len(ids) <= 50 or any(type(i) is not int for i in ids):
        raise Invalid('Choose between 1 and 50 members')
    members = list(Membership.objects.filter(workspace=p.workspace, user_id__in=ids).select_related('user'))
    if len({m.user_id for m in members}) != len(set(ids)):
        raise Invalid('Some selected members are not in this workspace')
    if any(m.role != 'annotator' for m in members):
        raise Invalid('Tasks can only be distributed to annotators')
    scope = request.data.get('scope', 'unassigned')
    tasks = p.tasks.exclude(state__in=['submitted', 'approved'])
    if scope == 'unassigned':
        tasks = tasks.filter(assignee__isnull=True)
    elif scope != 'all':
        raise Invalid('Invalid distribution scope')
    tasks = list(tasks.order_by('?' if request.data.get('shuffle') is True else 'id'))
    if not tasks:
        raise Invalid('Nothing to distribute')
    now = timezone.now()
    for i, t in enumerate(tasks):
        t.assignee = members[i % len(members)].user
        t.revision += 1
        t.updated_at = now
    Task.objects.bulk_update(tasks, ['assignee', 'revision', 'updated_at'])
    event(request, p, 'tasks.distributed', payload={'users': [m.user.username for m in members], 'count': len(tasks), 'scope': scope})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def assign(request, pk):
    t, m = task_access(request, pk)
    if m.role not in ['owner', 'reviewer']:
        return JsonResponse({'error': 'Only managers can assign tasks'}, status=403)
    target = get_object_or_404(Membership, workspace=t.project.workspace, user_id=request.data['user'])
    if target.role != 'annotator':
        raise Invalid('Tasks can only be assigned to annotators')
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
    nxt = t.project.tasks.filter(state='submitted').order_by('id').first()
    return JsonResponse({'redirect': f'/tasks/{nxt.pk}/' if nxt else f'/projects/{t.project_id}/'})


@api
def comment(request, pk):
    t, _ = task_access(request, pk)
    note = request.data.get('note')
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise Invalid('Comment is required (maximum 2000 characters)')
    event(request, t.project, 'task.comment', t, {'text': note.strip()})
    return JsonResponse({'reload': True})


@api
@transaction.atomic
def restore(request, pk):
    t, m = task_access(request, pk)
    if t.state not in ['open', 'in_progress', 'changes_requested'] or (t.assignee_id != request.user.pk and m.role != 'owner'):
        return JsonResponse({'error': 'Task is not editable by you'}, status=403)
    revision = request.data.get('revision')
    if type(revision) is not int:
        raise Invalid('Revision is required')
    source = get_object_or_404(Event, pk=request.data.get('event_id'), task=t, action__in=['annotation.saved', 'task.submitted', 'annotation.restored'])
    annotation = validate_annotation(source.payload.get('annotation'), t.project)
    if not Task.objects.filter(pk=t.pk, revision=revision, state=t.state).update(annotation=annotation, revision=revision + 1):
        return JsonResponse({'error': 'Conflict: a newer revision exists. Reload before saving.'}, status=409)
    event(request, t.project, 'annotation.restored', t, {'revision': revision + 1, 'from_revision': source.payload.get('revision'), 'annotation': annotation})
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


def _release(request, pk):
    if request.user.is_superuser:
        return get_object_or_404(Release, pk=pk)
    return get_object_or_404(Release, pk=pk, project__workspace__membership__user=request.user)


@login_required
def release_download(request, pk):
    release = _release(request, pk)
    response = JsonResponse(release.snapshot, json_dumps_params={'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="release-{release.pk}.json"'
    response['Cache-Control'] = 'private, no-store'
    return response


def _polygon_area(points, width, height):
    area = 0
    for i, (x, y) in enumerate(points):
        nx, ny = points[(i + 1) % len(points)]
        area += x * width * ny * height - nx * width * y * height
    return abs(area) / 2


def snapshot_to_coco(snapshot, release):
    px = lambda n: round(n, 2)
    categories = [{'id': i + 1, 'name': l['name'], 'supercategory': l['key']} for i, l in enumerate(snapshot.get('labels', []))]
    category_id = {l['id']: i + 1 for i, l in enumerate(snapshot.get('labels', []))}
    images, annotations, relations = [], [], []
    ann_id = 0
    for task in snapshot.get('tasks', []):
        w, h = task.get('width', 0), task.get('height', 0)
        images.append({'id': task['id'], 'file_name': task['filename'], 'width': w, 'height': h})
        region_ids = {}
        for region in task.get('annotation', {}).get('regions', []):
            ann_id += 1
            ann = {'id': ann_id, 'image_id': task['id'], 'category_id': category_id.get(region['label'], 0),
                   'iscrowd': 0, 'attributes': region.get('attributes', {}), 'region_id': region['id'], 'region_type': region['type']}
            if region['type'] == 'box':
                (x0, y0), (x1, y1) = region['points']
                ann['bbox'] = [px(x0 * w), px(y0 * h), px((x1 - x0) * w), px((y1 - y0) * h)]
                ann['area'] = px(ann['bbox'][2] * ann['bbox'][3])
            elif region['type'] == 'polygon':
                ann['segmentation'] = [[px(c * s) for point in region['points'] for c, s in zip(point, (w, h))]]
                ann['area'] = px(_polygon_area(region['points'], w, h))
            else:
                x, y = region['points'][0]
                ann['keypoints'] = [px(x * w), px(y * h), 2]
                ann['num_keypoints'] = 1
                ann['bbox'] = [px(x * w - 1), px(y * h - 1), 2, 2]
                ann['area'] = 4
            annotations.append(ann)
            region_ids[region['id']] = ann_id
        for rel in task.get('annotation', {}).get('relations', []):
            relations.append({'from': region_ids[rel['from']], 'to': region_ids[rel['to']], 'key': rel['key'], 'image_id': task['id']})
    return {'info': {'description': snapshot.get('project'), 'release': release.name, 'schema_version': snapshot.get('schema_version'),
                     'source_format': 'annotation-studio/v1', 'date_created': release.created_at.isoformat()},
            'images': images, 'annotations': annotations, 'categories': categories, 'relations': relations}


@login_required
def release_coco(request, pk):
    release = _release(request, pk)
    response = JsonResponse(snapshot_to_coco(release.snapshot, release), json_dumps_params={'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="release-{release.pk}-coco.json"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def export_zip(request, pk):
    p, m = project_access(request, pk)
    if m.role not in ['owner', 'reviewer']:
        return HttpResponseForbidden('Exports are limited to owners and reviewers')
    tasks = [t for t in p.tasks.select_related('assignee').order_by('id') if t.annotation.get('regions')]
    if not tasks:
        return JsonResponse({'error': 'Nothing is labeled yet — annotate at least one image first.'}, status=400)
    snapshot = {'format': 'annotation-studio/v1', 'draft': True, 'exported_at': timezone.now().isoformat(),
                'project': {'id': p.pk, 'name': p.name, 'geometry': p.geometry, 'schema_version': p.schema_version},
                'labels': list(p.labels.values('id', 'key', 'name', 'color', 'attributes')),
                'tasks': [{'id': t.pk, 'filename': t.filename, 'digest': t.digest, 'width': t.width, 'height': t.height,
                           'state': t.state, 'assignee': t.assignee.username if t.assignee else None,
                           'revision': t.revision, 'annotation': t.annotation} for t in tasks]}
    snapshot = json.loads(json.dumps(snapshot, default=str))
    tmp = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('annotations.json', json.dumps(snapshot, indent=2))
        for t in tasks:
            safe = re.sub(r'[^\w.-]', '_', t.filename)
            with t.image.open('rb') as f:
                z.writestr(f'images/{t.pk}-{safe}', f.read(), compress_type=zipfile.ZIP_STORED)
    tmp.seek(0)
    slug = re.sub(r'[^\w-]+', '-', p.name).strip('-').lower() or 'project'
    response = FileResponse(tmp, as_attachment=True, filename=f'{slug}-export.zip', content_type='application/zip')
    response['Cache-Control'] = 'private, no-store'
    return response


def snapshot_to_yolo(snapshot):
    labels = snapshot.get('labels', [])
    category_index = {l['id']: i for i, l in enumerate(labels)}
    names = '\n'.join(f'  {i}: {l["name"]}' for i, l in enumerate(labels))
    texts = {'data.yaml': f"path: .\ntrain: images\nval: images\n\nnames:\n{names}\n",
             'README.txt': ('YOLOv8 dataset export from Godezk Marker.\n\n'
                            'Region mapping (all coordinates normalized 0-1):\n'
                            '  box     -> "class cx cy w h" detection row\n'
                            '  polygon -> "class x1 y1 x2 y2 ..." segmentation row\n'
                            '  point   -> "class x y 0.004 0.004" small box on the point\n\n'
                            'Label file stems match image file stems.\n')}
    images = {}
    for task in snapshot.get('tasks', []):
        stem = re.sub(r'[^\w.-]', '_', task.get('filename', 'image').rsplit('.', 1)[0])
        stem = f"{task['id']}-{stem}"
        lines = []
        for region in task.get('annotation', {}).get('regions', []):
            cls = category_index.get(region['label'], 0)
            points = region['points']
            if region['type'] == 'box':
                (x0, y0), (x1, y1) = points
                lines.append(f"{cls} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}")
            elif region['type'] == 'polygon':
                lines.append(f"{cls} " + ' '.join(f"{x:.6f} {y:.6f}" for x, y in points))
            else:
                x, y = points[0]
                lines.append(f"{cls} {x:.6f} {y:.6f} 0.004000 0.004000")
        texts[f'labels/{stem}.txt'] = '\n'.join(lines)
        images[f'images/{stem}.jpg'] = task['id']
    return texts, images


@login_required
def release_yolo(request, pk):
    release = _release(request, pk)
    texts, images = snapshot_to_yolo(release.snapshot)
    tmp = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for arcname, content in texts.items():
            z.writestr(arcname, content)
        for arcname, task_id in images.items():
            task = Task.objects.filter(pk=task_id).first()
            if task and task.image:
                with task.image.open('rb') as f:
                    z.writestr(arcname, f.read(), compress_type=zipfile.ZIP_STORED)
    tmp.seek(0)
    response = FileResponse(tmp, as_attachment=True, filename=f'release-{release.pk}-yolo.zip', content_type='application/zip')
    response['Cache-Control'] = 'private, no-store'
    return response


def api_auth(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        header = request.headers.get('Authorization', '')
        token_value = header[7:].strip() if header.startswith('Bearer ') else ''
        digest = hashlib.sha256(token_value.encode()).hexdigest()
        token = ApiToken.objects.select_related('workspace', 'user').filter(key_hash=digest, revoked=False).first()
        if not token or not token.user.is_active:
            return JsonResponse({'error': 'Invalid or revoked API token'}, status=401)
        token.last_used_at = timezone.now()
        token.save(update_fields=['last_used_at'])
        request.api_token = token
        return fn(request, *args, **kwargs)
    return wrapped


@api_auth
def api_projects(request):
    projects = Project.objects.filter(workspace=request.api_token.workspace).order_by('id')
    return JsonResponse({'projects': [{'id': p.pk, 'name': p.name, 'geometry': p.geometry,
                                       'schema_version': p.schema_version, 'tasks': p.tasks.count()} for p in projects]})


@api_auth
def api_project_detail(request, pk):
    p = get_object_or_404(Project, pk=pk, workspace=request.api_token.workspace)
    return JsonResponse({'id': p.pk, 'name': p.name, 'instructions': p.instructions, 'geometry': p.geometry,
                         'schema_version': p.schema_version, 'created_at': p.created_at.isoformat(),
                         'labels': list(p.labels.values('id', 'key', 'name', 'color', 'attributes', 'archived')),
                         'task_counts': dict(p.tasks.values_list('state').annotate(n=Count('id')).values_list('state', 'n'))})


@api_auth
def api_tasks(request, pk):
    p = get_object_or_404(Project, pk=pk, workspace=request.api_token.workspace)
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
        limit = min(500, max(1, int(request.GET.get('limit', 100))))
    except ValueError:
        return JsonResponse({'error': 'Invalid offset/limit'}, status=400)
    tasks = p.tasks.select_related('assignee').order_by('id')[offset:offset + limit]
    return JsonResponse({'total': p.tasks.count(), 'offset': offset, 'limit': limit,
                         'tasks': [{'id': t.pk, 'filename': t.filename, 'state': t.state, 'revision': t.revision,
                                    'width': t.width, 'height': t.height, 'regions': len(t.annotation.get('regions', [])),
                                    'assignee': t.assignee.username if t.assignee else None} for t in tasks]})


@api_auth
def api_task(request, pk):
    t = get_object_or_404(Task.objects.select_related('project'), pk=pk, project__workspace=request.api_token.workspace)
    return JsonResponse({'id': t.pk, 'project_id': t.project_id, 'filename': t.filename, 'state': t.state,
                         'revision': t.revision, 'width': t.width, 'height': t.height,
                         'annotation': t.annotation, 'updated_at': t.updated_at.isoformat()})


@api_auth
def api_release(request, pk):
    release = get_object_or_404(Release, pk=pk, project__workspace=request.api_token.workspace)
    return JsonResponse(release.snapshot)
