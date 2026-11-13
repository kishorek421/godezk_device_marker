# Deployment

## Processes

A production install runs **three** long-lived processes:

| Process | Command | Purpose |
|---|---|---|
| Web | `gunicorn config.wsgi -w 4 -b 0.0.0.0:8000` (Linux) · `waitress-serve --port=8000 config.wsgi:application` (Windows) | Serves the app + static files via WhiteNoise |
| Worker | `python manage.py runworker` | Executes jobs: webhooks, `agent.propose` auto-labeling |
| Model server (optional) | `python model_server.py` | YOLO predictions for auto-labeling — only needed if AI labeling is used |

## First deploy

```bash
pip install -r requirements.txt          # + requirements-agent.txt on the model host only
export DJANGO_SECRET_KEY=<64+ random chars>
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS=your.domain.com
export DJANGO_CSRF_ORIGINS=https://your.domain.com
export DJANGO_TRUST_PROXY=1              # only if behind nginx/ELB setting X-Forwarded-Proto

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python manage.py check --deploy          # must print "no issues"
```

## Scale knobs

- **~100 concurrent annotators** → switch to Postgres: set `POSTGRES_*` vars (see `.env.example`), `migrate`, then `dumpdata`/`loaddata` to move data. Job claiming (`skip_locked`) already supports multiple workers — run 2–4 `runworker` processes.
- **~1M assets** → move uploads to object storage: set `AWS_STORAGE_BUCKET_NAME` + credentials.
- **DB connections** → `DJANGO_CONN_MAX_AGE=300` reuses connections under load (Postgres only).

## Backups

```bash
python manage.py backupdata --out backups/     # SQLite: tar of SQL dump + media
pg_dump -Fc godezk > backup.dump               # Postgres: use pg_dump instead
```

## Monitoring

`GET /health/` — anonymous gets `{"status":"ok"}`; superuser gets DB ping, task/job counts, `jobs_pending`, `jobs_dead`, `deliveries_failed`. Alert on `jobs_dead > 0` and queue growth.

## Auto-labeling model

`model_server.py` serves any Ultralytics `.pt` weights on `127.0.0.1:9000`.
Set `MODEL_WEIGHTS=your-model.pt` to change domains — the Django side needs no code changes, only the project's `agent_config` (`url`, `label_map`, `min_score`).
