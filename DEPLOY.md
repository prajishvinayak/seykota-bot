# Deploy the Seykota Bot on your Ubuntu (DigitalOcean) droplet

This bot is lightweight (runs once a day, a few seconds of work) and **fully isolated**
in its own folder + virtualenv, so it co-exists safely with your other bot.

**Account safety:** this bot pins itself to the **"Ed Seykota" (£21K)** account every cycle
(`mode.account_name` in `config.yaml`). ⚠️ Make sure your **other bot is pinned to the
£11K account** — the £21K one is the login's "preferred", so a bot that auto-picks
"preferred" would grab £21K and collide with this one.

---

## 1. Copy the bot to the droplet
From your PC (PowerShell), e.g. with scp (or use git):
```powershell
scp -r "C:\Users\AddyP\Claude\Projects\Ed Seykota Bot" root@YOUR_DROPLET_IP:/opt/seykota-bot
```
> Do **not** copy your local `.env`, `cache*/`, `state/` — recreate `.env` fresh on the droplet.

## 2. Install (isolated venv)
```bash
ssh root@YOUR_DROPLET_IP
cd /opt/seykota-bot
bash deploy/deploy_ubuntu.sh
```

## 3. Add your credentials
```bash
nano /opt/seykota-bot/.env      # paste the 3 Capital.com demo values (see .env.example)
chmod 600 /opt/seykota-bot/.env
```

## 4. Test before scheduling
```bash
cd /opt/seykota-bot
.venv/bin/python check_connection.py      # should log in + show "Ed Seykota" £21k
.venv/bin/python run_paper.py --dry-run   # one cycle, NO orders
```

## 5. Schedule it (cron, daily, weekdays)
DigitalOcean droplets run on **UTC**. Open the crontab:
```bash
crontab -e
```
Add this line (15:05 UTC = US+Europe open; **staggered 5 min** so it never runs at the
exact moment your other bot switches accounts):
```cron
5 15 * * 1-5  /opt/seykota-bot/deploy/run_once.sh
```
Make the wrapper executable:
```bash
chmod +x /opt/seykota-bot/deploy/run_once.sh
```

## 6. Watch it
```bash
tail -f /opt/seykota-bot/logs/cron.log          # daily cycle output
cat /opt/seykota-bot/output/eod_report_live.md  # the report to paste to Claude
```
The live dashboard is `output/dashboard_live.html` — copy it back to view, or serve the
`output/` folder with `python3 -m http.server` if you want it in a browser.

## 7. Controls
```bash
touch /opt/seykota-bot/KILL_SWITCH    # next cycle flattens all + halts
rm    /opt/seykota-bot/KILL_SWITCH    # resume
/opt/seykota-bot/.venv/bin/python run_paper.py --flatten   # close everything now
```

---

## Resource check (smallest droplets)
This bot spikes ~150–250 MB RAM during its daily cycle (pandas). If you're on a 512 MB
droplet running another bot too, add swap:
```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```
Or resize to the 1 GB droplet ($6/mo). Check current usage with `free -h`.

## Two bots, one droplet — isolation checklist
- ✅ Separate folders (`/opt/seykota-bot` vs the other bot's dir)
- ✅ Separate virtualenvs (this script makes its own `.venv`)
- ✅ Separate `.env`, `state/`, `output/`, `logs/`
- ✅ **Separate Capital.com accounts** (£21K here, £11K other) — the important one
- ✅ Staggered cron times (this one at :05) so account switches never race
