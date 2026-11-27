"""Local YOLO prediction service for Godezk Marker auto-labeling.

Serves POST /predict for the studio 'http' predictor. Model-agnostic:
any Ultralytics-compatible .pt weights file works — swap MODEL_WEIGHTS
to change domains (helmet detection today, anything else tomorrow)
without touching the labeling pipeline.

Also serves POST /embed (image_b64 -> feature vector) for the studio
'similarity' predictor — matching new detections against regions a
human has already labeled ("label a few, it learns the rest").

Env:
  MODEL_WEIGHTS  path to .pt file (default: helmet.pt, fallback yolov8n.pt)
  HOST           bind address (default: 0.0.0.0)
  PORT           listen port (default: 9000)

Run:  python model_server.py
"""
import base64
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEIGHTS = os.environ.get('MODEL_WEIGHTS', 'helmet.pt')
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '9000'))

from ultralytics import YOLO  # noqa: E402

model = YOLO(WEIGHTS if os.path.exists(WEIGHTS) else 'yolov8n.pt')
print(f'Model: {model.ckpt_path if hasattr(model, "ckpt_path") else WEIGHTS}')
print(f'Classes: {model.names}')

_embedder = None


def get_embedder():
    """Lazily load a MobileNetV3 feature extractor for /embed."""
    global _embedder
    if _embedder is None:
        import torch
        import torchvision
        net = torchvision.models.mobilenet_v3_small(weights='DEFAULT')
        net.classifier = torch.nn.Identity()
        net.eval()
        _embedder = net
    return _embedder


def embed_image(image):
    import torch
    import torchvision.transforms as T
    tf = T.Compose([
        T.Resize((224, 224)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    with torch.no_grad():
        vec = get_embedder()(tf(image.convert('RGB')).unsqueeze(0))[0]
    vec = vec / vec.norm()
    return vec.tolist()


class Predict(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ('/predict', '/embed'):
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            from PIL import Image
            image = Image.open(io.BytesIO(base64.b64decode(body['image_b64'])))
            if self.path == '/embed':
                self._reply({'embedding': embed_image(image)})
                return
            results = model.predict(image, verbose=False)[0]
            names = model.names
            w, h = image.size
            detections = []
            for b in results.boxes:
                x1, y1, x2, y2 = (v / s for v, s in zip(b.xyxy[0].tolist(), [w, h, w, h]))
                detections.append({'label': names[int(b.cls)], 'type': 'box',
                                   'points': [[x1, y1], [x2, y2]], 'score': round(float(b.conf), 4)})
            self._reply({'detections': detections})
        except Exception as exc:
            self._reply({'detections': [], 'error': str(exc)}, status=500)

    def do_GET(self):
        if self.path == '/health':
            self._reply({'status': 'ok', 'classes': list(model.names.values())})
        else:
            self.send_error(404)

    def _reply(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    print(f'Listening on http://{HOST}:{PORT}/predict')
    ThreadingHTTPServer((HOST, PORT), Predict).serve_forever()
