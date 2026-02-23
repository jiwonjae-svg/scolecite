# Maintenance Commands — Oracle Cloud VPS

Quick reference for daily operations. Full guide: [DEPLOYMENT.md](../DEPLOYMENT.md).

---

## Deployment

| Action | Command |
|--------|---------|
| Full deploy | `cd /opt/scolecite && ./deploy.sh` |
| Deploy (no git pull) | `./deploy.sh --no-pull` |
| Status | `./deploy.sh --status` |
| Live logs | `./deploy.sh --logs` |
| Backup DB | `./deploy.sh --backup-db` |
| Manual restart | `docker compose restart server` |
| Full rebuild | `docker compose up -d --build --force-recreate` |

---

## Logs

| Source | Command |
|--------|---------|
| All containers | `docker compose logs -f --tail=100` |
| Server only | `docker compose logs -f server` |
| DB only | `docker compose logs -f db` |
| systemd service | `journalctl -u scolecite -f` |
| Nginx access | `sudo tail -f /var/log/nginx/scolecite_access.log` |
| Nginx error | `sudo tail -f /var/log/nginx/scolecite_error.log` |

---

## Database

| Action | Command |
|--------|---------|
| Backup | `./deploy.sh --backup-db` |
| Manual dump | `docker compose exec -T db pg_dump -U scolecite scolecite \| gzip > backup.sql.gz` |
| Restore | `gunzip -c backup.sql.gz \| docker compose exec -T db psql -U scolecite scolecite` |
| Shell | `docker compose exec db psql -U scolecite scolecite` |
| Size | `docker compose exec db psql -U scolecite -c "SELECT pg_size_pretty(pg_database_size('scolecite'));"` |

---

## Docker

| Action | Command |
|--------|---------|
| Container stats | `docker stats --no-stream` |
| Disk usage | `docker system df` |
| Prune images | `docker image prune -f` |
| Prune all (careful) | `docker system prune -f` |

---

## Security

| Action | Command |
|--------|---------|
| Fail2Ban status | `sudo fail2ban-client status sshd` |
| Banned IPs | `sudo fail2ban-client status sshd \| grep "Banned IP"` |
| Unban IP | `sudo fail2ban-client set sshd unbanip 1.2.3.4` |
| UFW status | `sudo ufw status numbered` |
| Auth logs | `sudo tail -50 /var/log/auth.log` |

---

## SSL (Let's Encrypt)

| Action | Command |
|--------|---------|
| Check expiry | `sudo certbot certificates` |
| Renew (dry-run) | `sudo certbot renew --dry-run` |
| Force renew | `sudo certbot renew --force-renewal` |

---

## System

| Action | Command |
|--------|---------|
| Resources | `htop` or `free -h && df -h` |
| Uptime | `uptime` |
| Reboot | `sudo reboot` (containers auto-start via systemd) |
