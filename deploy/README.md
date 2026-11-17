# deploy/ — manifests for `godezk_device_devops`

Tailored to this cluster's existing conventions (per-app Postgres like
mlops/attendance/vehicle/task-manager, shared MinIO, Kustomize overlays,
one ArgoCD Application per service on branch `dev`).

## Where they go

```
godezk_device_devops/
└── k8s/services/device_marker/
    ├── base/            # existing deployment/service + postgres.yaml
    └── overlays/dev/    # env-specific patches, kustomization.yaml
```

Add an ArgoCD Application for the postgres resources if the existing
`device-marker-dev` app doesn't already cover the whole folder.

## Apply order

1. `secrets.example.yaml` → fill `POSTGRES_PASSWORD`, `url` (same pw),
   `django-secret-key` → `kubectl apply` → store values in secrets
   manager, not git
2. `postgres.yaml` → `device-marker-postgres` Deployment + ClusterIP
   Service + 5Gi `local-path` PVC. Postgres **self-initializes** from
   `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (task-manager style)
3. Merge `deployment-env.yaml` block into the app Deployment — Django
   env, `POSTGRES_*` via `secretKeyRef`, MinIO S3 vars for uploads
4. Create the `device-marker-uploads` bucket in MinIO (one-time, via
   console at NodePort 30700 or `mc`)
5. Rollout → `kubectl exec` → `python manage.py seed_demo` → first users

## Alternatives

- **Managed Postgres** instead of `postgres.yaml`: point `POSTGRES_HOST`
  at it, keep everything else
- **PVC instead of MinIO** for uploads: use `uploads-pvc.yaml`, mount
  `/app/private_uploads`, drop the `AWS_*` env block

## Notes

- Cluster Postgres traffic is plain TCP → `POSTGRES_SSLMODE=disable`
- No backups exist on any app DB today — if annotations matter, add a
  `pg_dump` CronJob; nothing else on the cluster has one either
- Later: worker deployment (`python manage.py runworker`) for
  auto-label jobs, and a reachable model endpoint — not blocking
