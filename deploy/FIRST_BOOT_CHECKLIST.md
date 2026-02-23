# First Boot Checklist — Oracle Cloud VPS

Run through this checklist after your first deployment. Full guide: [DEPLOYMENT.md](../DEPLOYMENT.md).

---

## Pre-Deployment

- [ ] Oracle Cloud A1 instance created (4 OCPU, 24 GB RAM, Ubuntu 24.04)
- [ ] Security List: ports 22, 80, 443 open (Ingress)
- [ ] SSH key added to instance
- [ ] Public IP noted

---

## System

- [ ] SSH into server: `ssh ubuntu@YOUR_IP`
- [ ] System updated: `sudo apt update && sudo apt upgrade -y`
- [ ] Timezone set: `sudo timedatectl set-timezone Asia/Seoul`
- [ ] Hostname set: `sudo hostnamectl set-hostname scolecite-prod`
- [ ] Optional swap: `sudo fallocate -l 4G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`

---

## Security

- [ ] UFW enabled: `sudo ufw allow 22,80,443/tcp && sudo ufw enable`
- [ ] Fail2Ban installed and running: `sudo systemctl status fail2ban`
- [ ] Unattended upgrades: `sudo dpkg-reconfigure -plow unattended-upgrades`

---

## Docker

- [ ] Docker installed: `docker --version && docker compose version`
- [ ] User in docker group: `groups` (should include docker)
- [ ] `docker compose up -d` works

---

## Application

- [ ] Repo cloned: `ls /opt/scolecite/docker-compose.yml`
- [ ] `.env` configured: `cp .env.example .env` and fill API keys
- [ ] `.env` permissions: `chmod 600 .env`
- [ ] `settings.json` exists (optional, created on first run)

---

## Containers

- [ ] `docker compose ps` — both `db` and `server` running
- [ ] `docker compose exec db pg_isready -U scolecite` — DB healthy
- [ ] `curl http://localhost:8000/health` — `{"status":"ok"}`
- [ ] `curl http://localhost:8000/ready` — `{"status":"ready","db":"connected"}`

---

## Nginx

- [ ] Nginx installed: `sudo systemctl status nginx`
- [ ] `deploy/nginx/scolecite.conf` copied to `/etc/nginx/sites-available/scolecite`
- [ ] Symlink: `sudo ln -sf /etc/nginx/sites-available/scolecite /etc/nginx/sites-enabled/`
- [ ] `YOUR_DOMAIN_OR_IP` replaced in config
- [ ] `sudo nginx -t && sudo systemctl reload nginx`
- [ ] External access: `curl http://YOUR_IP/health`

---

## SSL (if using domain)

- [ ] Certbot installed: `sudo apt install certbot python3-certbot-nginx`
- [ ] `sudo certbot certonly --webroot -w /var/www/certbot -d YOUR_DOMAIN --email YOUR_EMAIL`
- [ ] SSL paths updated in nginx config
- [ ] `curl -I https://YOUR_DOMAIN/health`

---

## systemd

- [ ] `sudo cp deploy/scolecite.service /etc/systemd/system/`
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable scolecite`
- [ ] Reboot test: `sudo reboot` → after reboot, `curl http://localhost:8000/health`

---

## Functional Tests

- [ ] Desktop client connects (Settings → Server URL)
- [ ] `curl -N http://localhost:8000/api/stream` — SSE stream works
- [ ] `curl -X POST http://localhost:8000/api/bot/start` — bot starts
- [ ] `curl http://localhost:8000/api/cost` — AI cost returned
- [ ] `./deploy.sh --backup-db` — backup succeeds

---

## Optional: Cron

- [ ] Health watchdog: `*/5 * * * * curl -sf http://localhost:8000/health || (cd /opt/scolecite && docker compose restart server)`
- [ ] Daily backup: `0 4 * * * cd /opt/scolecite && ./deploy.sh --backup-db`
