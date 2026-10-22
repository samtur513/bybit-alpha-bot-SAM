# Issue 05: Web Dashboard v3 (Port 8082) & Deployment Automation

Status: completed
Type: task

## Goal
Build `v3/web_dashboard.py` and deployment scripts:
- Dedicated Port 8082 to allow parallel A/B/C testing with v2.0 (8080) and v2.1 (8081).
- Live visualization of dynamic universe ranking, relative strength scores, and News Shield status.
- Webhook `POST /api/news_event` for live external alert injection.
- Shell scripts: `setup_v3.sh`, `start.sh`, `stop.sh`, `status.sh`, `restart.sh`, `logs.sh`.
Completed and ready for deployment.
