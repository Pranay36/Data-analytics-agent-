# Deploying

Backend on one EC2 machine, frontend on Vercel. The browser only ever talks to the Vercel
domain: Next.js forwards `/api/v1/*` to the backend, so the login cookie is first-party and
there is no CORS to configure.

```
Browser ──HTTPS──> Vercel (Next.js) ──HTTPS──> Caddy ──> api ──> appdb + demo-analytics
                      rewrites /api/v1/*         EC2, private docker network
```

## 1. The EC2 machine

- Ubuntu 24.04, **t3.small or larger** (1 GB is not enough for the API plus two databases)
- An Elastic IP, so the address survives a reboot
- Security group: **80 and 443** open to the world, **22** open only to your own IP.
  Never open 5432 or 5433 — the databases are reachable only from inside the machine.

```bash
scp deploy/setup-ec2.sh ubuntu@<elastic-ip>:~
ssh ubuntu@<elastic-ip> 'sh setup-ec2.sh'   # installs Docker, adds swap; log out and back in
```

## 2. A hostname for the API

Caddy needs a real hostname to get an HTTPS certificate. Without a domain, use
[sslip.io](https://sslip.io): an IP of `13.233.10.20` becomes `13-233-10-20.sslip.io`, which
resolves straight back to it. With a domain, point an `A` record at the Elastic IP instead.

## 3. Secrets on the server

```bash
scp deploy/.env.prod.example ubuntu@<ip>:/opt/insightflow/deploy/.env
ssh ubuntu@<ip> 'chmod 600 /opt/insightflow/deploy/.env && nano /opt/insightflow/deploy/.env'
```

Fill in every value. They live only on the machine — never in the repository or an image.

## 4. GitHub repository secrets

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `EC2_HOST` | the Elastic IP |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | the **private** key for that machine, whole file including header and footer |
| `API_HOST` | the hostname from step 2 |

Then run **Deploy backend** from the Actions tab. It builds the image, pushes it to
ghcr.io, copies the compose files over, restarts the stack, and checks the public HTTPS
address — rolling back to the previous image if that check fails.

## 5. Vercel

Import the repository, set **Root Directory** to `frontend`, and add one environment variable:

```
BACKEND_URL = https://<API_HOST>
```

Deploy. Then put the Vercel URL into `WEB_ORIGIN` in the server's `.env` and redeploy the
backend, so it recognises the site.

## Day to day

```bash
ssh ubuntu@<ip>
cd /opt/insightflow/deploy
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml ps
```

Back up the application database (users and analyses) with:

```bash
docker compose -f docker-compose.prod.yml exec -T appdb \
    pg_dump -U insightflow insightflow | gzip > backup-$(date +%F).sql.gz
```
