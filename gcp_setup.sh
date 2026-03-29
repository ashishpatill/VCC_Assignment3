#!/usr/bin/env bash
# =============================================================================
# gcp_setup.sh — VCC Assignment 3: GCP Infrastructure Setup Script
# =============================================================================
# Author: Ashish
# Course: Virtual Cloud Computing (VCC) — Assignment 3
#
# Description:
#   This script sets up the complete GCP infrastructure needed for auto-scaling:
#     1. Enables required APIs
#     2. Creates a firewall rule to allow Flask traffic
#     3. Creates an Instance Template (blueprint for cloud VMs)
#     4. Creates a Managed Instance Group (fleet of VMs)
#     5. Attaches an Autoscaler (scales 1–5 VMs at 75% CPU target)
#     6. (Optional) Lists all created resources for verification
#
# Prerequisites:
#   - gcloud CLI installed: https://cloud.google.com/sdk/docs/install
#   - Authenticated: run `gcloud init` and `gcloud auth login`
#   - A GCP project with billing enabled
#
# Usage:
#   chmod +x gcp_setup.sh          ← make it executable (run once)
#   ./gcp_setup.sh                 ← run the setup
#   ./gcp_setup.sh --cleanup       ← delete all resources (avoids charges)
#
# =============================================================================

set -euo pipefail
# set -e  → Stop immediately if any command fails
# set -u  → Stop if an undefined variable is used
# set -o pipefail → A pipe fails if any command in the pipe fails


# ─────────────────────────────────────────────
# 🔧 Configuration — Edit These Values
# ─────────────────────────────────────────────

PROJECT_ID="vcc-assignment3"          # Your GCP project ID
REGION="us-central1"                  # GCP region (choose one close to you)
ZONE="us-central1-a"                  # Specific zone within the region

TEMPLATE_NAME="vcc-flask-template"    # Name for the Instance Template
MIG_NAME="vcc-flask-mig"             # Name for the Managed Instance Group
FIREWALL_RULE="allow-flask-traffic"  # Name for the firewall rule

MACHINE_TYPE="e2-medium"             # VM size: 2 vCPU, 4 GB RAM (~$0.03/hour)
DISK_SIZE="20GB"                     # Boot disk size
IMAGE_FAMILY="ubuntu-2204-lts"       # OS: Ubuntu 22.04 LTS
IMAGE_PROJECT="ubuntu-os-cloud"      # Google's official Ubuntu image project

MIN_REPLICAS=1                       # Minimum number of VMs (always keep 1 up)
MAX_REPLICAS=5                       # Maximum number of VMs (cost cap)
CPU_TARGET=0.75                      # Scale up when CPU exceeds 75%
COOLDOWN_PERIOD=60                   # Seconds to wait after scaling before checking again

# GitHub repo where your code lives (update this after pushing to GitHub)
GITHUB_REPO="https://github.com/YOUR_USERNAME/vcc_assignment3.git"


# ─────────────────────────────────────────────
# 🎨 Helper Functions (colors for output)
# ─────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'   # No Color (reset)

info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

step()    {
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  STEP: $1${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}


# ─────────────────────────────────────────────
# 🧹 Cleanup Mode (--cleanup flag)
# ─────────────────────────────────────────────
# Run this when you're done with the assignment to avoid ongoing GCP charges!

if [[ "${1:-}" == "--cleanup" ]]; then
    echo ""
    warn "CLEANUP MODE — Deleting all VCC Assignment 3 GCP resources..."
    warn "This will DELETE VMs, templates, and firewall rules."
    echo -n "Are you sure? (yes/no): "
    read -r confirm
    if [[ "$confirm" != "yes" ]]; then
        info "Cleanup cancelled."
        exit 0
    fi

    info "Deleting Managed Instance Group: $MIG_NAME..."
    gcloud compute instance-groups managed delete "$MIG_NAME" \
        --zone="$ZONE" \
        --project="$PROJECT_ID" \
        --quiet || warn "MIG not found (may already be deleted)."

    info "Deleting Instance Template: $TEMPLATE_NAME..."
    gcloud compute instance-templates delete "$TEMPLATE_NAME" \
        --project="$PROJECT_ID" \
        --quiet || warn "Template not found."

    info "Deleting Firewall Rule: $FIREWALL_RULE..."
    gcloud compute firewall-rules delete "$FIREWALL_RULE" \
        --project="$PROJECT_ID" \
        --quiet || warn "Firewall rule not found."

    success "Cleanup complete! All VCC Assignment 3 resources deleted."
    info "Tip: Verify nothing remains at https://console.cloud.google.com/compute"
    exit 0
fi


# ─────────────────────────────────────────────
# 🚀 Main Setup
# ─────────────────────────────────────────────

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║  VCC Assignment 3 — GCP Auto-Scaling Setup         ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""
info "Project  : $PROJECT_ID"
info "Zone     : $ZONE"
info "Machine  : $MACHINE_TYPE"
info "Min VMs  : $MIN_REPLICAS"
info "Max VMs  : $MAX_REPLICAS"
info "CPU Target: ${CPU_TARGET} (75%)"


# ── Pre-flight: Set active project ───────────────────────────────────────────
step "0 — Set Active GCP Project"

gcloud config set project "$PROJECT_ID"
success "Active project set to: $PROJECT_ID"


# ── Step 1: Enable Required GCP APIs ─────────────────────────────────────────
step "1 — Enable Required GCP APIs"

# Compute Engine API: needed to create VMs, templates, instance groups
# Cloud Monitoring API: needed for auto-scaling metrics
info "Enabling Compute Engine API..."
gcloud services enable compute.googleapis.com \
    --project="$PROJECT_ID"

info "Enabling Cloud Monitoring API..."
gcloud services enable monitoring.googleapis.com \
    --project="$PROJECT_ID"

success "APIs enabled."


# ── Step 2: Create Firewall Rule ──────────────────────────────────────────────
step "2 — Create Firewall Rule (Allow Port 5000)"

# A firewall rule controls which network traffic can reach your VMs.
# By default, GCP blocks all inbound traffic.
# We need to allow TCP port 5000 so users can access the Flask app.

# Check if the rule already exists to avoid errors on re-run
if gcloud compute firewall-rules describe "$FIREWALL_RULE" \
        --project="$PROJECT_ID" &>/dev/null; then
    warn "Firewall rule '$FIREWALL_RULE' already exists. Skipping."
else
    gcloud compute firewall-rules create "$FIREWALL_RULE" \
        --project="$PROJECT_ID" \
        --allow="tcp:5000" \
        --source-ranges="0.0.0.0/0" \
        --target-tags="flask-server" \
        --description="Allow inbound traffic to Flask app on port 5000"
    # --allow tcp:5000    → Allow TCP connections on port 5000
    # --source-ranges     → From anywhere on the internet (0.0.0.0/0)
    # --target-tags       → Only applies to VMs with the tag "flask-server"

    success "Firewall rule created: $FIREWALL_RULE"
fi


# ── Step 3: Create Instance Template ─────────────────────────────────────────
step "3 — Create Instance Template"

# An Instance Template is like a recipe card for VMs.
# When the autoscaler needs a new VM, it uses this template to create one
# with exactly the right OS, disk size, machine type, and startup script.

# The startup script runs automatically when a VM boots.
# It installs Python, clones your code from GitHub, and starts Flask.
STARTUP_SCRIPT="#!/bin/bash
set -e

# Log startup progress to a file (useful for debugging)
exec > /var/log/startup-script.log 2>&1

echo '=== VCC Assignment 3 Startup Script ==='
echo 'Updating system packages...'
apt-get update -y
apt-get install -y python3-pip python3-venv git

echo 'Cloning project repository...'
cd /opt
git clone ${GITHUB_REPO} vcc_assignment3 || {
    echo 'Git clone failed. Check GITHUB_REPO variable in gcp_setup.sh'
    exit 1
}

cd vcc_assignment3

echo 'Creating virtual environment...'
python3 -m venv venv
source venv/bin/activate

echo 'Installing Python dependencies...'
pip install -r requirements.txt

echo 'Starting Flask app...'
# nohup keeps Flask running after the startup script exits
nohup python3 app.py > /var/log/flask-app.log 2>&1 &

echo 'Flask app started on port 5000'
echo 'Startup complete!'
"

if gcloud compute instance-templates describe "$TEMPLATE_NAME" \
        --project="$PROJECT_ID" &>/dev/null; then
    warn "Instance template '$TEMPLATE_NAME' already exists. Skipping."
else
    gcloud compute instance-templates create "$TEMPLATE_NAME" \
        --project="$PROJECT_ID" \
        --machine-type="$MACHINE_TYPE" \
        --image-family="$IMAGE_FAMILY" \
        --image-project="$IMAGE_PROJECT" \
        --boot-disk-size="$DISK_SIZE" \
        --boot-disk-type="pd-balanced" \
        --tags="flask-server" \
        --metadata="startup-script=$STARTUP_SCRIPT"
    # --machine-type      → e2-medium: 2 vCPUs, 4 GB RAM
    # --image-family      → Ubuntu 22.04 LTS (latest in this family)
    # --image-project     → Google's official Ubuntu image project
    # --boot-disk-size    → 20 GB disk
    # --boot-disk-type    → pd-balanced = good performance/cost balance
    # --tags              → Network tag; matches the firewall rule we created
    # --metadata          → Startup script runs on every boot

    success "Instance template created: $TEMPLATE_NAME"
fi


# ── Step 4: Create Managed Instance Group ────────────────────────────────────
step "4 — Create Managed Instance Group (MIG)"

# A Managed Instance Group (MIG) is a group of VMs that are:
# - Created from the same Instance Template (all identical)
# - Automatically healed if one fails (GCP restarts it)
# - Automatically scaled up/down based on load
#
# We start with 1 instance (the minimum), and let the Autoscaler add more.

if gcloud compute instance-groups managed describe "$MIG_NAME" \
        --zone="$ZONE" \
        --project="$PROJECT_ID" &>/dev/null; then
    warn "MIG '$MIG_NAME' already exists. Skipping creation."
else
    gcloud compute instance-groups managed create "$MIG_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --template="$TEMPLATE_NAME" \
        --size="$MIN_REPLICAS"
    # --template    → Use our Instance Template as the blueprint
    # --size        → Start with this many VMs (we start at min = 1)

    success "Managed Instance Group created: $MIG_NAME"
fi


# ── Step 5: Attach the Autoscaler ────────────────────────────────────────────
step "5 — Configure Autoscaler"

# The Autoscaler watches CPU utilization across all VMs in the MIG.
# - If average CPU > 75% → add more VMs (up to MAX_REPLICAS)
# - If average CPU < 75% → remove VMs (down to MIN_REPLICAS)
# - The cool-down period prevents "flapping" (rapid add/remove cycles)

gcloud compute instance-groups managed set-autoscaling "$MIG_NAME" \
    --project="$PROJECT_ID" \
    --zone="$ZONE" \
    --min-num-replicas="$MIN_REPLICAS" \
    --max-num-replicas="$MAX_REPLICAS" \
    --target-cpu-utilization="$CPU_TARGET" \
    --cool-down-period="$COOLDOWN_PERIOD"
# --min-num-replicas       → Always have at least this many VMs (cost floor)
# --max-num-replicas       → Never exceed this many VMs (cost ceiling)
# --target-cpu-utilization → Target 75% CPU utilization across the group
# --cool-down-period       → Wait 60s after scaling before making another decision

success "Autoscaler configured on MIG: $MIG_NAME"


# ── Step 6: Verify Everything ─────────────────────────────────────────────────
step "6 — Verify Setup"

info "Listing Instance Templates:"
gcloud compute instance-templates list \
    --project="$PROJECT_ID" \
    --filter="name=$TEMPLATE_NAME"

info "Listing Managed Instance Groups:"
gcloud compute instance-groups managed list \
    --project="$PROJECT_ID" \
    --zones="$ZONE"

info "Listing VM Instances in MIG (may take 1-2 min to appear):"
gcloud compute instance-groups managed list-instances "$MIG_NAME" \
    --zone="$ZONE" \
    --project="$PROJECT_ID"

# ── Final Summary ─────────────────────────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  ✅ GCP Setup Complete!                                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
success "Instance Template : $TEMPLATE_NAME"
success "Managed Instance Group: $MIG_NAME (zone: $ZONE)"
success "Autoscaler: min=$MIN_REPLICAS max=$MAX_REPLICAS target=${CPU_TARGET}"
echo ""
info "Next steps:"
info "  1. Wait ~2 minutes for the initial VM to boot and start Flask."
info "  2. Get the external IP:  gcloud compute instances list"
info "  3. Test Flask:           curl http://EXTERNAL_IP:5000/"
info "  4. Test auto-scaling:    curl http://EXTERNAL_IP:5000/stress"
info "  5. Watch scaling:        gcloud compute instance-groups managed list-instances $MIG_NAME --zone=$ZONE"
echo ""
warn "IMPORTANT: Run './gcp_setup.sh --cleanup' when done to avoid ongoing charges!"
echo ""
