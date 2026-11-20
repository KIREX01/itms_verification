# Central server: production web server (gunicorn + nginx)

`run_web` uses Django's development server. It's fine on a laptop with a few users, but not for
`close.kirex.online`. On the central server, run **gunicorn** behind nginx instead.

## Why these settings
- **One worker, many threads** (`--workers 1 --threads 16 -k gthread`). The ITMS sync daemons and the
  check-in handler live inside the web process (`itms_project/wsgi.py`). Several worker *processes* would each
  start their own sync daemons and sync ITMS several times over. Threads keep one copy, and still give a bounded
  thread pool, keep-alive and production-grade request handling.
- **The vision pipeline runs in its own low-priority process** (v1.3.0), so it doesn't compete with web threads.
- **nginx serves `/static/`** straight from disk with long cache headers. `/media/` (evidence photos) still goes
  through Django, because every photo request must be signed in. Django now sends `ETag` and
  `Cache-Control: private` so browsers don't download the same photo again.

## Switch over (on the server, as root)
```bash
cd /opt/itms
sudo -u www-data .venv/bin/python manage.py collectstatic --noinput
cp deploy/linux/itms-gunicorn.service /etc/systemd/system/itms.service   # replaces the run_web unit
systemctl daemon-reload && systemctl restart itms && systemctl status itms --no-pager
```
Add the `location /static/` block from `deploy/linux/nginx-close.conf` to the site, then run `nginx -t && systemctl reload nginx`.

**Roll back:** restore the previous unit (`/etc/systemd/system/itms.service.bak`), then run `systemctl daemon-reload && systemctl restart itms`.
