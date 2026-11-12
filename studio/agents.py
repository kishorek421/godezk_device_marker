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
    regions = []
    scores = []
    for det in data.get('detections', []):
        if det.get('score', 1) < min_score:
            continue
        key = label_map.get(det.get('label'), det.get('label'))
        scores.append(round(det.get('score', 0), 3))
        regions.append({'id': _region_id(len(regions)), 'label': key,
                        'type': det.get('type', 'box'), 'points': det.get('points', []),
                        'attributes': {}})
    return {'regions': regions, 'relations': [], 'confidence': scores}
