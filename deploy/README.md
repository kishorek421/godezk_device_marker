# deploy/ — manifests for the DevOps repo

Hand these files to whoever owns `godezk_device_devops`. They map to
`k8s/services/device_marker/` — adjust names/namespaces/storage class
to match the cluster conventions.

## Apply order

1. `secrets.example.yaml` — fill real values, `kubectl apply`, keep the
   filled copy OUT of git (store values in the secrets manager)
2. `postgres.yaml` — Postgres StatefulSet + Service + 20Gi PVC
3. `uploads-pvc.yaml` — 50Gi PVC for `/app/private_uploads`
4. `deployment-env.yaml` — commented env/volume block to merge into the
   existing device-marker Deployment
5. Rollout → `kubectl exec` → `python manage.py seed_demo` for first users

## Alternative: managed Postgres instead of postgres.yaml

If the org already has RDS/CloudSQL/a Postgres cluster, skip
`postgres.yaml` and point `POSTGRES_HOST` at it. Everything else stays.

## Alternative: S3 instead of uploads-pvc.yaml

Set `AWS_STORAGE_BUCKET_NAME` + `AWS_ACCESS_KEY_ID` +
`AWS_SECRET_ACCESS_KEY` (+ `AWS_S3_ENDPOINT_URL` for MinIO) in env and
skip the uploads PVC. App switches to object storage automatically.

## Later: job worker + model server

Auto-labeling needs a second container/deployment on the same image with
command `python manage.py runworker`, and a reachable model endpoint for
the `http` predictor. Not blocking — the platform works without it.
