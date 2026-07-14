# MediSuite Cloud Backup Server

A tiny, standalone Flask app you (the developer) deploy separately from
MediSuite itself. Any MediSuite desktop install can optionally push an
encrypted database backup here, and pull it back down onto a *replacement*
PC if the original machine dies - restoring by clinic-chosen username and
password, not by machine ID.

This is completely optional. An install that never sets up cloud backup
makes zero extra network calls and nothing changes.

## What it stores

Per account: `username`, a salted password hash (for login only - this
server never receives the clinic's actual backup password), and one
**already client-side encrypted** blob of their latest database backup.

**This server never sees plaintext.** MediSuite encrypts the backup on the
clinic's own PC, using a key derived from their backup password, before
ever sending it here. Without that password, the encrypted blob is useless -
not even you, with full database access, can read customer names, phone
numbers, addresses, or insurance details out of it. There is no password
recovery: if a clinic loses their backup password, that backup is gone.

Only the **latest** backup per account is kept (not a history) - local
MediSuite backups already provide retention/history; this is just an
off-site safety copy.

## Deploy (Render.com free web service + Neon free Postgres - recommended)

Render's *own* free Postgres auto-deletes 30 days after creation (14-day
grace period, then gone) - not viable for this. Use Neon's free tier
instead, which has no such expiration:

1. Create a free project at [neon.tech](https://neon.tech). Copy its
   connection string (starts with `postgresql://...`).
2. On [render.com](https://render.com), create a new **Web Service** pointed
   at this folder (same pattern as `status_server/` - a separate repo whose
   root is this folder's contents, or a monorepo with this as the root
   directory).
3. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`
4. Add one environment variable in Render's dashboard:
   - `DATABASE_URL` - the Neon connection string from step 1
5. Render gives you a URL like `https://medisuite-cloud-backup.onrender.com`.

Tables are created automatically on first request - no separate migration
step needed.

## Point your MediSuite installs at it

Set one environment variable before launching (or add it to your packaging
script's environment):

```
MEDISUITE_CLOUD_BACKUP_URL=https://medisuite-cloud-backup.onrender.com
```

Then, from the app's Backups page, a clinic can sign up for a cloud backup
account (username + password of their choosing), trigger "Backup Now to
Cloud," and enable automatic scheduled cloud backups the same way local
auto-backup already works. On a replacement PC, they log in with the same
username + password to pull their latest backup back down.

## Free-tier capacity note

Neon's free tier caps out at 0.5 GB total storage. At roughly 10-30 MB per
clinic's database, that comfortably supports dozens of installs before
you'd need to upgrade Neon's plan.
