"""Pluggable AI predictors for pre-labeling.

A predictor is a callable ``predict(task, config) -> dict`` that returns an
annotation document in the canonical ``annotation-studio/v1`` shape
(``{'regions': [...], 'relations': []}``). Predictors are registered by name
with the ``@predictor`` decorator; a project selects one via
``project.agent_predictor`` and passes options via ``project.agent_config``.

Predictions are always validated through the same ``validate_annotation``
path as human work and land as ordinary annotations for a human to verify —
the model never bypasses review.
"""
import base64
import json
import urllib.request

PREDICTORS = {}


def predictor(name):
    def register(func):
        PREDICTORS[name] = func
        return func
    return register


def _region_id(index):
    return f'ai-{index}'


@predictor('http')
def predict_http(task, config):
    """Generic HTTP predictor — works with any model behind a JSON endpoint.

    agent_config keys:
      url         (required) endpoint that accepts POST {image_b64, filename}
      headers     optional dict of extra headers (e.g. Authorization)
      label_map   optional {model_class: label_key} — classes not mapped are skipped
      min_score   optional float, detections below are dropped (default 0)
      timeout     seconds, default 30

    Expected response: {"detections": [{"label": "helmet", "type": "box",
      "points": [[x1,y1],[x2,y2]], "score": 0.9}]} with normalized coords.
    """
    url = config.get('url')
    if not url:
        raise RuntimeError('agent_config.url is required for the http predictor')
    with task.image.open('rb') as f:
        image_b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({'image_b64': image_b64, 'filename': task.filename,
                       'width': task.width, 'height': task.height}).encode()
    headers = {'Content-Type': 'application/json', **(config.get('headers') or {})}
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=config.get('timeout', 30)) as response:
        data = json.loads(response.read())
    label_map = config.get('label_map') or {}
    min_score = float(config.get('min_score', 0))
    # detections name project label keys — resolve them to the label pks
    # validate_annotation requires
    by_key = {l.key: str(l.pk) for l in task.project.labels.filter(archived=False)}
    regions = []
    scores = []
    for det in data.get('detections', []):
        if det.get('score', 1) < min_score:
            continue
        key = label_map.get(det.get('label'), det.get('label'))
        label_pk = by_key.get(key)
        if not label_pk:
            continue  # detection for a class the project doesn't track
        scores.append(round(det.get('score', 0), 3))
        regions.append({'id': _region_id(len(regions)), 'label': label_pk,
                        'type': det.get('type', 'box'), 'points': det.get('points', []),
                        'attributes': {}})
    return {'regions': regions, 'relations': [], 'confidence': scores}


def _post_json(url, payload, timeout):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _crop_b64(task, points):
    """Crop the bounding box of normalized region points from a task image;
    returns base64 PNG."""
    import io
    from PIL import Image
    with task.image.open('rb') as f:
        img = Image.open(f).convert('RGB')
    w, h = img.size
    xs = [p[0] * w for p in points]
    ys = [p[1] * h for p in points]
    x1, y1, x2, y2 = max(0, min(xs)), max(0, min(ys)), min(w, max(xs)), min(h, max(ys))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    buf = io.BytesIO()
    img.crop((x1, y1, x2, y2)).save(buf, 'PNG')
    return base64.b64encode(buf.getvalue()).decode()


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # inputs are L2-normalized


@predictor('similarity')
def predict_similarity(task, config):
    """Match detections to regions a human already labeled in this project —
    "label a few examples, it learns the rest" (e.g. recognize a specific
    person/part once it's been labeled on a handful of tasks).

    agent_config keys:
      detector_url   required — detection endpoint: POST {image_b64} -> {detections}
      embed_url      required — embedder endpoint:  POST {image_b64} -> {embedding}
      min_score      optional detector confidence floor (default 0.25)
      min_similarity optional cosine-similarity floor to accept a match (default 0.65)
      max_exemplars  optional per-label exemplar cap (default 12, oldest first)
      label_map      optional {detector_class: label_key} fallback when no
                     exemplar matches, e.g. new objects nothing has taught it
    """
    detector_url = config.get('detector_url')
    embed_url = config.get('embed_url')
    if not detector_url or not embed_url:
        raise RuntimeError('similarity predictor needs agent_config detector_url and embed_url')
    timeout = config.get('timeout', 30)
    labels = {str(l.pk): l.key for l in task.project.labels.filter(archived=False)}

    # Gather exemplar crops from regions already annotated on other tasks.
    max_ex = int(config.get('max_exemplars', 12))
    per_label = {}
    exemplars = []  # (label_pk, embedding)
    for t in task.project.tasks.exclude(pk=task.pk).exclude(annotation={}):
        for region in (t.annotation or {}).get('regions', []):
            label_pk = region.get('label')
            if label_pk not in labels or not region.get('points'):
                continue
            if per_label.get(label_pk, 0) >= max_ex:
                continue
            crop = _crop_b64(t, region['points'])
            if not crop:
                continue
            vec = _post_json(embed_url, {'image_b64': crop}, timeout).get('embedding')
            if vec:
                exemplars.append((label_pk, vec))
                per_label[label_pk] = per_label.get(label_pk, 0) + 1

    # Detect objects in the new image, then classify each by nearest exemplar.
    with task.image.open('rb') as f:
        image_b64 = base64.b64encode(f.read()).decode()
    data = _post_json(detector_url, {'image_b64': image_b64, 'filename': task.filename,
                                     'width': task.width, 'height': task.height}, timeout)
    min_score = float(config.get('min_score', 0.25))
    min_sim = float(config.get('min_similarity', 0.65))
    label_map = config.get('label_map') or {}
    by_key = {l.key: str(l.pk) for l in task.project.labels.filter(archived=False)}

    regions = []
    scores = []
    for det in data.get('detections', []):
        if det.get('score', 1) < min_score or not det.get('points'):
            continue
        label_pk = None
        similarity = 0.0
        if exemplars:
            crop = _crop_b64(task, det['points'])
            if crop:
                vec = _post_json(embed_url, {'image_b64': crop}, timeout).get('embedding')
                if vec:
                    label_pk, similarity = max(
                        ((lp, _cosine(vec, ev)) for lp, ev in exemplars), key=lambda x: x[1])
                    if similarity < min_sim:
                        label_pk = None
        if not label_pk:
            # fallback: detector class mapped to a project label (new/unseen objects)
            label_pk = by_key.get(label_map.get(det.get('label'), det.get('label')))
        if not label_pk:
            continue
        scores.append(round(float(similarity or det.get('score', 0)), 3))
        regions.append({'id': _region_id(len(regions)), 'label': label_pk,
                        'type': det.get('type', 'box'), 'points': det['points'],
                        'attributes': {}})
    return {'regions': regions, 'relations': [], 'confidence': scores}
