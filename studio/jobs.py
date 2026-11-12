"""Background job queue with retry and dead-lettering.

Workers run ``manage.py runworker`` which polls :func:`run_pending`.
Jobs retry with exponential backoff and move to ``dead`` after
``max_attempts`` so nothing is silently lost — dead jobs stay visible
for investigation and can be re-queued.
"""
import hashlib
import hmac
import json
import urllib.request
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Event, Job, Task, Webhook, WebhookDelivery

HANDLERS = {}
STALE_AFTER = timedelta(minutes=10)


def handler(job_type):
    def register(func):
        HANDLERS[job_type] = func
        return func
    return register


def enqueue(job_type, payload=None, max_attempts=3, delay_seconds=0):
    return Job.objects.create(type=job_type, payload=payload or {}, max_attempts=max_attempts,
                              run_after=timezone.now() + timedelta(seconds=delay_seconds))


def run_pending(limit=20):
    # Reclaim jobs whose worker died mid-flight — nothing is permanently lost
    stale = timezone.now() - STALE_AFTER
    Job.objects.filter(status='running', updated_at__lt=stale).update(status='pending', run_after=timezone.now())
    ran = 0
    while ran < limit:
        # Atomically claim one pending job — concurrent workers can't double-run it
        with transaction.atomic():
            job = (Job.objects.select_for_update(skip_locked=True)
                   .filter(status='pending', run_after__lte=timezone.now()).order_by('run_after').first())
            if not job:
                return ran
            job.status = 'running'
            job.attempts += 1
            job.save(update_fields=['status', 'attempts', 'updated_at'])
        try:
            result = HANDLERS[job.type](job)
            job.status, job.result, job.error = 'done', result or {}, ''
        except KeyError:
            job.status, job.error = 'dead', f'Unknown job type: {job.type}'
        except Exception as exc:
            job.error = str(exc)[:500]
            if job.attempts >= job.max_attempts:
                job.status = 'dead'
            else:
                job.status = 'pending'
                job.run_after = timezone.now() + timedelta(seconds=30 * (2 ** (job.attempts - 1)))
        job.save()
        ran += 1
    return ran


def post_signed(url, secret, body):
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    headers = {'Content-Type': 'application/json', 'X-Godezk-Event': body['event'],
               'X-Godezk-Signature': f'sha256={signature}'}
    with urllib.request.urlopen(urllib.request.Request(url, data=raw, headers=headers), timeout=5) as response:
        return response.status


@handler('webhook.deliver')
def deliver_webhook(job):
    delivery = WebhookDelivery.objects.select_related('webhook').get(pk=job.payload['delivery_id'])
    code = post_signed(delivery.webhook.url, delivery.webhook.secret, job.payload['body'])
    if not 200 <= code < 300:
        raise RuntimeError(f'Webhook endpoint returned {code}')
    delivery.status, delivery.response_code, delivery.error = 'delivered', code, ''
    delivery.save(update_fields=['status', 'response_code', 'error'])
    return {'delivery': str(delivery.pk), 'code': code}


@handler('agent.propose')
def propose_annotations(job):
    """Run a project's predictor on one task and store the proposal as an
    annotation for a human to verify. Proposals go through the same schema
    validation and revision guard as human saves, and never auto-submit."""
    from .agents import PREDICTORS
    from .views import validate_annotation
    task = Task.objects.select_related('project').get(pk=job.payload['task_id'])
    if task.state not in ['open', 'in_progress', 'changes_requested']:
        return {'skipped': f'task state is {task.state}'}
    predictor = PREDICTORS.get(task.project.agent_predictor)
    if not predictor:
        raise RuntimeError(f'Unknown predictor: {task.project.agent_predictor!r}')
    proposal = predictor(task, task.project.agent_config or {})
    confidence = proposal.pop('confidence', [])
    annotation = validate_annotation(proposal, task.project)
    changed = Task.objects.filter(pk=task.pk, revision=task.revision, state=task.state).update(
        annotation=annotation, revision=task.revision + 1, state='in_progress')
    if not changed:
        return {'skipped': 'revision changed under us — requeue if still needed'}
    Event.objects.create(project=task.project, task=task, actor=None, action='agent.proposed',
                         payload={'predictor': task.project.agent_predictor,
                                  'regions': len(annotation['regions']), 'confidence': confidence})
    return {'task': task.pk, 'regions': len(annotation['regions'])}
