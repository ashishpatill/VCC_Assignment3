# VCC Assignment 3 — Local VM + Resource Monitoring + GCP Auto-Scaling

**Student:** Ashish | **Course:** Virtual Cloud Computing (VCC)

> A hybrid cloud auto-scaling demo: monitor a local VirtualBox VM and automatically scale out to GCP when CPU/RAM/Disk exceeds 75%.

---

## Repository Structure

```
vcc_assignment3/
│
├── app.py                 ← Flask web app (/ , /status, /stress endpoints)
├── monitor.py             ← Resource monitor (CPU/RAM/Disk → GCP trigger)
├── requirements.txt       ← Python dependencies
├── gcp_setup.sh           ← Shell script to set up all GCP infrastructure
│
├── report.md              ← Full assignment report (step-by-step, beginner-friendly)
├── architecture.html      ← Interactive architecture diagram (Mermaid.js)
├── gcp_console_steps.md   ← GCP web console guide (no CLI needed)
└── README.md              ← This file
```

---

## Quick Start (Local VM)

### 1. Clone this repo inside your Ubuntu VM

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/vcc_assignment3.git
cd vcc_assignment3
```

### 2. Set up Python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the Flask app

```bash
python3 app.py
```

Open in your browser (from host machine, using port forwarding):
- `http://localhost:5000/` — health check
- `http://localhost:5000/status` — live resource stats (JSON)
- `http://localhost:5000/stress` — trigger 10-second CPU stress test

### 4. Run the Resource Monitor (in a separate terminal)

```bash
source venv/bin/activate
python3 monitor.py
```

You'll see live output every 10 seconds:
```
[INFO]  ✅ OK  — CPU:  12.3% | RAM:  45.1% | Disk:  24.1%
```

### 5. Trigger the Auto-Scale Alert

Visit `http://localhost:5000/stress` — watch the monitor detect high CPU and print:
```
[WARNING] ⚠️  THRESHOLD EXCEEDED — CPU:  89.7% | RAM:  46.2% | Disk:  24.1%
[WARNING]    🚨 ALERT: CPU at 89.7% (threshold: 75.0%)
[INFO]     📡 Triggering GCP scale-up...
```

---

## GCP Setup

### Option A: Using the CLI Script (Recommended)

```bash
# 1. Install and authenticate gcloud CLI first
gcloud init

# 2. Edit gcp_setup.sh — set your PROJECT_ID and GITHUB_REPO
nano gcp_setup.sh

# 3. Make it executable and run
chmod +x gcp_setup.sh
./gcp_setup.sh

# 4. When done with the assignment — CLEAN UP to avoid charges!
./gcp_setup.sh --cleanup
```

### Option B: Using the Web Console

See `gcp_console_steps.md` for a detailed point-and-click guide through the GCP web console.

---

## Architecture Overview

```
Local VM (VirtualBox)
  └── Flask App (app.py)
  └── Python Monitor (monitor.py)
        └── psutil reads CPU / RAM / Disk every 10 seconds
        └── If any > 75% → trigger GCP scale-up

GCP Cloud
  └── Instance Template (VM blueprint)
  └── Managed Instance Group
        └── Autoscaler (target 75% CPU, min 1 VM, max 5 VMs)
        └── Load Balancer (distributes traffic)
```

Open `architecture.html` in a browser for the full interactive diagram.

---

## Key Files Explained

| File | What It Does |
|---|---|
| `app.py` | Flask app with 3 endpoints. `/stress` spikes CPU for testing. |
| `monitor.py` | Reads CPU/RAM/Disk every 10s. Logs alerts and triggers GCP when >75%. |
| `requirements.txt` | Lists Python packages (flask, psutil, requests). |
| `gcp_setup.sh` | Automates all GCP resource creation (template → MIG → autoscaler). |
| `report.md` | Full assignment report with all steps explained. |
| `gcp_console_steps.md` | Beginner-friendly web console guide with screenshots guide. |

---

## Configuration

Edit the top of `monitor.py` to change:
```python
THRESHOLD_PERCENT = 75.0   # Change alert threshold
CHECK_INTERVAL    = 10     # Seconds between checks
GCP_PROJECT_ID    = "vcc-assignment3"
GCP_MIG_NAME      = "vcc-flask-mig"
```

---

## Important: Clean Up GCP Resources

GCP charges for running VMs. When you're done with the assignment:

```bash
./gcp_setup.sh --cleanup
```

Or manually in the console: **Compute Engine → Instance Groups → Delete** the MIG, then delete the template.

---

## Tech Stack

- **Python 3** + psutil + Flask
- **VirtualBox** + Ubuntu 22.04 LTS
- **Google Cloud Platform** (Compute Engine, Managed Instance Groups, Autoscaler)
- **gcloud CLI**
