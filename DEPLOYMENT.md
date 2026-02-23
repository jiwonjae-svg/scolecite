# Oracle Cloud VPS — Full Deployment Guide

> **Target:** Oracle Cloud Always Free Ampere A1 Compute  
> **Specs:** 4 OCPU (ARM64), 24 GB RAM, 200 GB Block Volume  
> **OS:** Ubuntu 24.04 LTS (aarch64)  
> **Stack:** Docker + docker-compose + PostgreSQL + Nginx + Let's Encrypt

---

## Table of Contents

1. [Oracle Cloud Setup](#1-oracle-cloud-setup)
2. [Initial Server Configuration](#2-initial-server-configuration)
3. [Security Hardening](#3-security-hardening)
4. [Install Docker](#4-install-docker)
5. [Install Nginx](#5-install-nginx)
6. [Deploy Scolecite](#6-deploy-scolecite)
7. [SSL with Let's Encrypt](#7-ssl-with-lets-encrypt)
8. [systemd Auto-Start](#8-systemd-auto-start)
9. [First Boot Checklist](#9-first-boot-checklist)
10. [Maintenance Commands](#10-maintenance-commands)
11. [Monitoring & Alerts](#11-monitoring--alerts)
12. [Troubleshooting](#12-troubleshooting)
13. [Backup & Restore](#13-backup--restore)
14. [Cost Summary](#14-cost-summary)

---

## 1. Oracle Cloud Setup

### 1.1 Create an Always Free A1 Instance

1. Log in to [Oracle Cloud Console](https://cloud.oracle.com/)
2. Navigate to **Compute → Instances → Create Instance**
3. Configure:

| Setting | Value |
|---------|-------|
| **Name** | `scolecite-prod` |
| **Image** | Canonical Ubuntu 24.04 (aarch64) |
| **Shape** | VM.Standard.A1.Flex |
| **OCPUs** | 4 |
| **Memory** | 24 GB |
| **Boot Volume** | 200 GB (Always Free allows up to 200 GB total) |
| **Network** | Default VCN + public subnet |
| **SSH Key** | Upload your `~/.ssh/id_rsa.pub` |

4. Click **Create** and wait for the instance to be **Running**

### 1.2 Configure Security Lists (Firewall)

In **Networking → Virtual Cloud Networks → Your VCN → Security Lists → Default**:

Add **Ingress Rules**:

| Source CIDR | Protocol | Dest Port | Description |
|-------------|----------|-----------|-------------|
| `0.0.0.0/0` | TCP | 22 | SSH |
| `0.0.0.0/0` | TCP | 80 | HTTP |
| `0.0.0.0/0` | TCP | 443 | HTTPS |

> ⚠️ **Important:** Oracle Cloud has TWO firewalls — the OCI Security List (cloud-level) AND iptables/UFW (OS-level). You must open ports in BOTH.

### 1.3 Note Your Public IP

```bash
# Find it in the OCI Console under your instance details
# Or after SSH:
curl -4 ifconfig.me
```

---

## 2. Initial Server Configuration

### 2.1 SSH into the Server

```bash
ssh -i ~/.ssh/id_rsa ubuntu@YOUR_PUBLIC_IP
```

### 2.2 System Update

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git wget unzip htop tmux jq
```

### 2.3 Set Timezone

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl
```

### 2.4 Set Hostname

```bash
sudo hostnamectl set-hostname scolecite-prod
```

### 2.5 Create Swap (Optional but Recommended)

Even with 24 GB RAM, a small swap prevents OOM kills:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify:
```bash
free -h
```

---

## 3. Security Hardening

### 3.1 UFW Firewall

```bash
sudo apt install -y ufw

# Default policies
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH, HTTP, HTTPS
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'

# Enable
sudo ufw enable
sudo ufw status verbose
```

### 3.2 Fail2Ban

```bash
sudo apt install -y fail2ban

# Create local config
sudo tee /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
backend  = systemd

[sshd]
enabled = true
port    = ssh
filter  = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime  = 86400
EOF

sudo systemctl enable fail2ban
sudo systemctl start fail2ban
sudo fail2ban-client status sshd
```

### 3.3 Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
# Select "Yes" when prompted

# Verify
cat /etc/apt/apt.conf.d/20auto-upgrades
# Should show:
# APT::Periodic::Update-Package-Lists "1";
# APT::Periodic::Unattended-Upgrade "1";
```

### 3.4 SSH Hardening (Optional)

```bash
sudo tee -a /etc/ssh/sshd_config.d/hardening.conf << 'EOF'
PasswordAuthentication no
PermitRootLogin no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

sudo systemctl restart sshd
```

> ⚠️ Make sure your SSH key works before disabling password auth!

---

## 4. Install Docker

### 4.1 Install Docker Engine

```bash
# Remove old versions
sudo apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add ubuntu user to docker group (no sudo needed for docker commands)
sudo usermod -aG docker ubuntu

# Apply group change (or log out and back in)
newgrp docker

# Verify
docker --version
docker compose version
```

### 4.2 Configure Docker Daemon

```bash
sudo tee /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "live-restore": true,
  "default-address-pools": [
    {"base": "172.17.0.0/16", "size": 24}
  ]
}
EOF

sudo systemctl restart docker
sudo systemctl enable docker
```

---

## 5. Install Nginx

```bash
sudo apt install -y nginx

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Copy Scolecite nginx config
sudo cp /opt/scolecite/deploy/nginx/scolecite.conf /etc/nginx/sites-available/scolecite

# Edit: replace YOUR_DOMAIN_OR_IP with your actual domain or IP
sudo nano /etc/nginx/sites-available/scolecite

# Enable the site
sudo ln -sf /etc/nginx/sites-available/scolecite /etc/nginx/sites-enabled/scolecite

# Test configuration
sudo nginx -t

# Start & enable
sudo systemctl enable nginx
sudo systemctl start nginx
```

### IP-Only Mode (No Domain)

If you don't have a domain name, edit the nginx config to use the IP-only fallback block:

1. Comment out the two HTTPS server blocks
2. Uncomment the IP-only fallback block at the bottom
3. `sudo nginx -t && sudo systemctl reload nginx`

---

## 6. Deploy Scolecite

### 6.1 Clone the Repository

```bash
sudo mkdir -p /opt/scolecite
sudo chown ubuntu:ubuntu /opt/scolecite

git clone https://github.com/jiwonjae-svg/scolecite.git /opt/scolecite
cd /opt/scolecite
```

### 6.2 Configure Environment

```bash
cp .env.example .env
nano .env
```

**Required changes in `.env`:**

```bash
# ---- AI API Keys ----
ANTHROPIC_API_KEY=sk-ant-your-key-here
XAI_GROK_API_KEY=xai-your-key-here

# ---- Broker (Alpaca) ----
APCA_API_KEY_ID=your-alpaca-key
APCA_API_SECRET_KEY=your-alpaca-secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets

# ---- Database (auto-configured by docker-compose, but set these) ----
POSTGRES_USER=scolecite
POSTGRES_PASSWORD=GENERATE_A_STRONG_PASSWORD_HERE
POSTGRES_DB=scolecite
# DATABASE_URL is overridden by docker-compose.yml automatically

# ---- Optional ----
POLYGON_API_KEY=your-polygon-key
TRADING_MODE=paper
```

Generate a strong password:
```bash
openssl rand -base64 32
```

### 6.3 Set File Permissions

```bash
chmod 600 .env
chmod +x deploy.sh
```

### 6.4 First Build & Launch

```bash
cd /opt/scolecite
docker compose up -d --build
```

Watch the logs:
```bash
docker compose logs -f
```

### 6.5 Verify

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"ok","mode":"paper"}

# Readiness check (DB connected)
curl http://localhost:8000/ready
# → {"status":"ready","db":"connected"}

# Full status
curl http://localhost:8000/api/status | jq .
```

---

## 7. SSL with Let's Encrypt

> Skip this section if you don't have a domain name. Use the IP-only nginx config instead.

### 7.1 Install Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
```

### 7.2 Create ACME Challenge Directory

```bash
sudo mkdir -p /var/www/certbot
```

### 7.3 Get Certificate

```bash
# Make sure nginx is running with the HTTP config
# and your domain's DNS A record points to this server's IP

sudo certbot certonly --webroot \
  -w /var/www/certbot \
  -d your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --no-eff-email
```

### 7.4 Update Nginx Config

Edit `/etc/nginx/sites-available/scolecite`:
- Replace `YOUR_DOMAIN_OR_IP` with your actual domain
- Replace `YOUR_DOMAIN` in the SSL certificate paths with your domain

```bash
sudo nano /etc/nginx/sites-available/scolecite
sudo nginx -t
sudo systemctl reload nginx
```

### 7.5 Auto-Renewal

Certbot installs a systemd timer automatically:

```bash
# Verify timer is active
sudo systemctl status certbot.timer

# Test renewal
sudo certbot renew --dry-run
```

### 7.6 Verify HTTPS

```bash
curl https://your-domain.com/health
```

---

## 8. systemd Auto-Start

### 8.1 Install the Service

```bash
sudo cp /opt/scolecite/deploy/scolecite.service /etc/systemd/system/scolecite.service
sudo systemctl daemon-reload
sudo systemctl enable scolecite
```

### 8.2 Service Commands

```bash
# Start
sudo systemctl start scolecite

# Stop
sudo systemctl stop scolecite

# Restart (rebuild + restart)
sudo systemctl reload scolecite

# Status
sudo systemctl status scolecite

# Logs
journalctl -u scolecite -f
journalctl -u scolecite --since "1 hour ago"
```

### 8.3 Verify Auto-Start on Reboot

```bash
sudo reboot
# After reboot, SSH back in:
sudo systemctl status scolecite
docker compose ps
curl http://localhost:8000/health
```

---

## 9. First Boot Checklist

Run through this checklist after your first deployment:

```
[ ] 1. SSH into server works
      ssh ubuntu@YOUR_IP

[ ] 2. System updated
      sudo apt update && sudo apt upgrade -y

[ ] 3. UFW firewall enabled
      sudo ufw status

[ ] 4. Fail2Ban running
      sudo fail2ban-client status sshd

[ ] 5. Docker installed and running
      docker --version && docker compose version

[ ] 6. Nginx installed and running
      sudo systemctl status nginx

[ ] 7. Repository cloned to /opt/scolecite
      ls /opt/scolecite/docker-compose.yml

[ ] 8. .env configured with all API keys
      cat /opt/scolecite/.env | grep -c "="

[ ] 9. .env permissions restricted
      stat -c %a /opt/scolecite/.env  # Should be 600

[ ] 10. Docker containers running
       docker compose ps

[ ] 11. PostgreSQL healthy
       docker compose exec db pg_isready -U scolecite

[ ] 12. Server health check passes
       curl http://localhost:8000/health

[ ] 13. Server readiness check passes (DB connected)
       curl http://localhost:8000/ready

[ ] 14. Nginx proxying correctly
       curl http://YOUR_IP/health  (or https://YOUR_DOMAIN/health)

[ ] 15. SSL certificate valid (if using domain)
       curl -I https://YOUR_DOMAIN/health

[ ] 16. systemd service enabled
       sudo systemctl is-enabled scolecite

[ ] 17. Auto-start on reboot verified
       sudo reboot  →  curl http://localhost:8000/health

[ ] 18. Desktop client connects
       Update client Settings → Server URL to https://YOUR_DOMAIN or http://YOUR_IP

[ ] 19. SSE streaming works
       curl -N http://localhost:8000/api/stream

[ ] 20. Bot starts in paper mode
       curl -X POST http://localhost:8000/api/bot/start

[ ] 21. AI cost monitoring works
       curl http://localhost:8000/api/cost

[ ] 22. Database backup works
       ./deploy.sh --backup-db
```

---

## 10. Maintenance Commands

### Daily Operations

```bash
# View live logs
cd /opt/scolecite && docker compose logs -f --tail=100

# View server logs only
docker compose logs -f server

# View database logs only
docker compose logs -f db

# Check container resource usage
docker stats

# Check disk usage
df -h /
docker system df
```

### Deployment

```bash
# Full deploy (git pull + rebuild + restart)
cd /opt/scolecite && ./deploy.sh

# Deploy without git pull (local changes only)
./deploy.sh --no-pull

# Quick status check
./deploy.sh --status

# Manual restart (no rebuild)
docker compose restart server

# Full rebuild
docker compose up -d --build --force-recreate
```

### Database

```bash
# Backup database
./deploy.sh --backup-db

# Manual backup
docker compose exec -T db pg_dump -U scolecite scolecite > backup.sql

# Restore from backup
cat backup.sql | docker compose exec -T db psql -U scolecite scolecite

# Connect to PostgreSQL shell
docker compose exec db psql -U scolecite scolecite

# Check database size
docker compose exec db psql -U scolecite -c "SELECT pg_size_pretty(pg_database_size('scolecite'));"
```

### Docker Cleanup

```bash
# Remove unused images
docker image prune -f

# Remove all unused data (careful!)
docker system prune -f

# Remove unused volumes (DANGEROUS - removes data!)
# docker volume prune -f
```

### Security

```bash
# Check Fail2Ban status
sudo fail2ban-client status sshd

# View banned IPs
sudo fail2ban-client status sshd | grep "Banned IP"

# Unban an IP
sudo fail2ban-client set sshd unbanip 1.2.3.4

# Check UFW status
sudo ufw status numbered

# View auth logs
sudo tail -50 /var/log/auth.log

# Check for security updates
sudo apt list --upgradable
```

### SSL Certificate

```bash
# Check certificate expiry
sudo certbot certificates

# Force renewal
sudo certbot renew --force-renewal

# Test renewal
sudo certbot renew --dry-run
```

### System

```bash
# Check system resources
htop
free -h
df -h

# Check uptime
uptime

# View systemd service logs
journalctl -u scolecite --since "24 hours ago"
journalctl -u scolecite -f

# Reboot (containers auto-start via systemd)
sudo reboot
```

---

## 11. Monitoring & Alerts

### Simple Uptime Check (cron)

```bash
# Add a cron job to check health every 5 minutes
crontab -e
```

Add this line:
```
*/5 * * * * curl -sf http://localhost:8000/health > /dev/null || (cd /opt/scolecite && docker compose restart server && echo "$(date): Server restarted" >> /var/log/scolecite-watchdog.log)
```

### Daily Database Backup (cron)

```bash
crontab -e
```

Add:
```
0 4 * * * cd /opt/scolecite && ./deploy.sh --backup-db >> /var/log/scolecite-backup.log 2>&1
```

### Log Rotation

Docker logs are already rotated via `json-file` driver config in `docker-compose.yml`.

For deploy logs:
```bash
sudo tee /etc/logrotate.d/scolecite << 'EOF'
/var/log/scolecite-*.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
EOF
```

---

## 12. Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs server --tail=50

# Check if port is in use
sudo lsof -i :8000
sudo lsof -i :5432

# Rebuild from scratch
docker compose down
docker compose up -d --build --force-recreate
```

### Database connection failed

```bash
# Check if DB container is running
docker compose ps db

# Check DB logs
docker compose logs db --tail=30

# Test connection manually
docker compose exec db psql -U scolecite -c "SELECT 1;"

# Check DATABASE_URL
docker compose exec server env | grep DATABASE
```

### Nginx 502 Bad Gateway

```bash
# Check if server container is running
docker compose ps server

# Check if server is listening on 8000
curl http://localhost:8000/health

# Check nginx error log
sudo tail -20 /var/log/nginx/scolecite_error.log

# Restart nginx
sudo systemctl restart nginx
```

### Out of memory

```bash
# Check memory usage
free -h
docker stats --no-stream

# Reduce Gunicorn workers in docker-compose.yml
# Change GUNICORN_WORKERS from 4 to 2

# Restart
docker compose up -d
```

### Disk full

```bash
# Check disk usage
df -h /
du -sh /var/lib/docker/

# Clean Docker
docker system prune -f
docker image prune -a -f

# Clean old logs
sudo journalctl --vacuum-time=7d
```

### Oracle Cloud iptables blocking traffic

Oracle Cloud Ubuntu images have iptables rules that may block traffic even with UFW:

```bash
# Check iptables
sudo iptables -L -n

# If ports 80/443 are blocked by iptables (not UFW):
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT

# Make persistent
sudo netfilter-persistent save
```

---

## 13. Backup & Restore

### Automated Backups

The `deploy.sh --backup-db` command:
- Creates a gzipped PostgreSQL dump
- Stores in `/opt/scolecite/backups/`
- Keeps the latest 14 backups
- Runs automatically before each deployment

### Manual Full Backup

```bash
# Database
docker compose exec -T db pg_dump -U scolecite scolecite | gzip > ~/scolecite_full_$(date +%Y%m%d).sql.gz

# Settings
cp /opt/scolecite/.env ~/scolecite_env_backup_$(date +%Y%m%d)
cp /opt/scolecite/settings.json ~/scolecite_settings_backup_$(date +%Y%m%d).json 2>/dev/null || true
```

### Restore Database

```bash
# Stop the server (keep DB running)
docker compose stop server

# Restore
gunzip -c backup_file.sql.gz | docker compose exec -T db psql -U scolecite scolecite

# Restart server
docker compose start server
```

### Migrate from SQLite to PostgreSQL

If you have an existing SQLite database from local development:

```bash
# On your local machine, export data
sqlite3 scolecite.db .dump > sqlite_dump.sql

# Copy to server
scp sqlite_dump.sql ubuntu@YOUR_IP:/tmp/

# On server, you'll need to manually adapt the SQL syntax
# SQLite → PostgreSQL differences (AUTOINCREMENT → SERIAL, etc.)
# Consider using pgloader for automated migration:
# https://pgloader.io/
```

---

## 14. Cost Summary

### Oracle Cloud Always Free Tier

| Resource | Allocation | Cost |
|----------|-----------|------|
| Ampere A1 Compute | 4 OCPU, 24 GB RAM | **$0/mo** |
| Block Volume | 200 GB | **$0/mo** |
| Outbound Data | 10 TB/mo | **$0/mo** |
| Object Storage | 20 GB | **$0/mo** |
| **Total Infrastructure** | | **$0/mo** |

### External Costs (Not Oracle)

| Service | Estimated Cost |
|---------|---------------|
| Domain name (optional) | ~$10-15/year |
| Anthropic API (Claude Opus) | Varies (tracked in-app) |
| xAI API (Grok) | Varies (tracked in-app) |
| Alpaca | Free (paper & live) |
| Polygon.io (optional) | Free tier available |

> 💡 **Total monthly cost: $0** for infrastructure (Always Free tier) + AI API usage costs only.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│              Oracle Cloud Always Free A1 Instance                │
│              Ubuntu 24.04 LTS (ARM64)                           │
│              4 OCPU · 24 GB RAM · 200 GB Disk                   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  UFW Firewall (22, 80, 443)                              │   │
│  │  Fail2Ban (SSH brute-force protection)                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────┐                                               │
│  │    Nginx     │  :80 → redirect to :443                       │
│  │  (host)      │  :443 → proxy to :8000                        │
│  │  Let's       │  SSL termination + rate limiting              │
│  │  Encrypt     │  SSE streaming support                        │
│  └──────┬───────┘                                               │
│         │ proxy_pass http://127.0.0.1:8000                      │
│         ▼                                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Docker Compose                                          │   │
│  │                                                          │   │
│  │  ┌────────────────────┐    ┌──────────────────────────┐  │   │
│  │  │  scolecite-server  │    │  scolecite-db            │  │   │
│  │  │  FastAPI + Gunicorn│───▶│  PostgreSQL 16            │  │   │
│  │  │  4 Uvicorn workers │    │  (Alpine)                │  │   │
│  │  │  :8000 (internal)  │    │  :5432 (internal)        │  │   │
│  │  └────────────────────┘    └──────────────────────────┘  │   │
│  │                                                          │   │
│  │  Volumes: pgdata · app_backups · app_data                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  systemd: scolecite.service (auto-start on boot)        │   │
│  │  cron: health watchdog (5min) + DB backup (daily 4AM)   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
          ▲
          │ HTTPS (REST + SSE streaming)
          │
┌─────────┴─────────┐
│  Desktop Client    │
│  customtkinter     │
└───────────────────┘
```
