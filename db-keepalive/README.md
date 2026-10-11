# DB keepalive

Runs a few read-only queries every 30 minutes so Aiven (or similar) sees activity.

## Render Cron Job

1. Render → **New** → **Cron Job**
2. Connect this repo
3. **Root Directory:** `db-keepalive`
4. **Schedule:** `*/30 * * * *` (every 30 minutes)
5. **Build:** `pip install -r requirements.txt`
6. **Start:** `python main.py`
7. **Environment:** paste the same `DATABASE_URL` as the backend (Aiven URL with `postgresql+asyncpg` is fine)

Free Render cron has limits; paid plans allow shorter intervals.

## Local test

```bash
cd db-keepalive
pip install -r requirements.txt
export DATABASE_URL='postgresql+asyncpg://...'
python main.py
```

## Queries

- `SELECT 1`
- `SELECT NOW()`
- `SELECT COUNT(*) FROM users`
- `SELECT COUNT(*) FROM orders`

If `users` / `orders` do not exist yet, those lines are skipped; the ping still runs.
