"""
monitor.py — VCC Assignment 3: Resource Monitor with GCP Auto-Scale Trigger
============================================================================
Author: Ashish
Course: Virtual Cloud Computing (VCC) — Assignment 3

Description:
    This script does TWO things simultaneously:

    1. MONITORING LOOP (every CHECK_INTERVAL seconds):
       - Reads CPU, RAM, Disk usage via psutil
       - Logs metrics to console + monitor.log
       - If any metric exceeds THRESHOLD_PERCENT (75%):
           → Logs an alert
           → Triggers GCP scale-up via gcloud CLI
           → Increments the Prometheus scaleup counter

    2. PROMETHEUS METRICS SERVER (runs on port 8000):
       - Exposes custom metrics at http://localhost:8000/metrics
       - Prometheus (running in Docker) scrapes this endpoint every 10s
       - Grafana reads from Prometheus to display live dashboards
       - Metrics exposed:
           vcc_cpu_percent      — current CPU usage %
           vcc_ram_percent      — current RAM usage %
           vcc_disk_percent     — current Disk usage %
           vcc_scaleup_total    — cumulative count of GCP scale-up triggers

Usage:
    python3 monitor.py

    # Run in background (keeps running after terminal closes):
    nohup python3 monitor.py >> monitor.log 2>&1 &

    # Check if it's running:
    ps aux | grep monitor.py

    # Stop it:
    kill $(pgrep -f monitor.py)

    # View Prometheus metrics it's exposing:
    curl http://localhost:8000/metrics
"""

import psutil           # Reads CPU, RAM, Disk metrics from the OS
import time             # For sleep between checks
import logging          # For structured log entries
import datetime         # For timestamps
import subprocess       # For calling gcloud CLI commands
import json             # For parsing gcloud JSON output
import os               # For file path operations
import threading        # For running Prometheus server + monitor loop concurrently

# prometheus_client: Official Python library for exposing Prometheus metrics
# Install: pip install prometheus-client
try:
    from prometheus_client import (
        start_http_server,   # Starts the /metrics HTTP server
        Gauge,               # A metric whose value can go up or down (e.g. CPU %)
        Counter,             # A metric that only ever increases (e.g. total alerts)
        Info                 # A metric that exposes text labels (e.g. version info)
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️  prometheus-client not installed. Run: pip install prometheus-client")
    print("   Prometheus metrics endpoint will be disabled. Monitor will still run.")


# ─────────────────────────────────────────────
# Configuration — Edit These to Match Your Setup
# ─────────────────────────────────────────────

THRESHOLD_PERCENT    = 75.0        # Alert if any metric exceeds this %
CHECK_INTERVAL       = 10          # Seconds between each resource check
LOG_FILE             = "monitor.log"
PROMETHEUS_PORT      = 8000        # Port for Prometheus /metrics endpoint

# GCP Settings — fill these in after Part D of the assignment
GCP_PROJECT_ID       = "vcc-assignment3"
GCP_ZONE             = "us-central1-a"
GCP_MIG_NAME         = "vcc-flask-mig"


# ─────────────────────────────────────────────
# Logger Setup
# Writes to BOTH console (live view) and monitor.log (permanent record)
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Prometheus Metrics Definitions
# ─────────────────────────────────────────────
# These are the custom metrics that monitor.py will expose at
# http://localhost:8000/metrics for Prometheus to scrape.
#
# Gauge:   value can go up or down — used for CPU/RAM/Disk %
# Counter: value only ever increases — used for scale-up trigger count
# ─────────────────────────────────────────────

if PROMETHEUS_AVAILABLE:
    # Current CPU usage percentage (0.0 – 100.0)
    PROM_CPU = Gauge(
        'vcc_cpu_percent',
        'Current CPU usage percentage on the local VM',
        ['host']             # Label: which host (useful in multi-VM setups)
    )

    # Current RAM usage percentage (0.0 – 100.0)
    PROM_RAM = Gauge(
        'vcc_ram_percent',
        'Current RAM usage percentage on the local VM',
        ['host']
    )

    # Current Disk usage percentage (0.0 – 100.0)
    PROM_DISK = Gauge(
        'vcc_disk_percent',
        'Current Disk usage percentage on the local VM (root partition)',
        ['host']
    )

    # Threshold value (so you can see it in Grafana as a reference line)
    PROM_THRESHOLD = Gauge(
        'vcc_threshold_percent',
        'Configured alert threshold percentage',
    )
    PROM_THRESHOLD.set(THRESHOLD_PERCENT)

    # Total number of times GCP scale-up has been triggered
    PROM_SCALEUP = Counter(
        'vcc_scaleup_total',
        'Total number of GCP scale-up triggers fired by monitor.py'
    )

    # App info (version, config) — useful for dashboards
    PROM_INFO = Info(
        'vcc_monitor',
        'VCC Assignment 3 monitor.py configuration info'
    )
    PROM_INFO.info({
        'version':     '1.0.0',
        'threshold':   str(THRESHOLD_PERCENT),
        'gcp_project': GCP_PROJECT_ID,
        'gcp_mig':     GCP_MIG_NAME
    })


# ─────────────────────────────────────────────
# Metric Collection
# ─────────────────────────────────────────────

HOSTNAME = os.uname().nodename   # e.g. "vcc-ubuntu"

def get_cpu_percent() -> float:
    """Returns CPU usage % averaged over a 1-second window."""
    return psutil.cpu_percent(interval=1)


def get_ram_percent() -> float:
    """Returns % of total RAM currently in use."""
    return psutil.virtual_memory().percent


def get_disk_percent(path: str = "/") -> float:
    """Returns % of disk space used at the given mount point."""
    return psutil.disk_usage(path).percent


def collect_metrics() -> dict:
    """
    Reads all three metrics from the OS and returns them as a dict.
    Also updates the Prometheus Gauge values so Prometheus can scrape them.
    """
    cpu  = get_cpu_percent()
    ram  = get_ram_percent()
    disk = get_disk_percent()
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Update Prometheus gauges every time we collect
    if PROMETHEUS_AVAILABLE:
        PROM_CPU.labels(host=HOSTNAME).set(cpu)
        PROM_RAM.labels(host=HOSTNAME).set(ram)
        PROM_DISK.labels(host=HOSTNAME).set(disk)

    return {"cpu": cpu, "ram": ram, "disk": disk, "timestamp": ts}


# ─────────────────────────────────────────────
# Threshold Check
# ─────────────────────────────────────────────

def check_thresholds(metrics: dict) -> list[str]:
    """
    Returns a list of threshold violations.
    Empty list = everything is fine.
    """
    exceeded = []
    if metrics["cpu"]  > THRESHOLD_PERCENT:
        exceeded.append(f"CPU at {metrics['cpu']:.1f}% (threshold: {THRESHOLD_PERCENT}%)")
    if metrics["ram"]  > THRESHOLD_PERCENT:
        exceeded.append(f"RAM at {metrics['ram']:.1f}% (threshold: {THRESHOLD_PERCENT}%)")
    if metrics["disk"] > THRESHOLD_PERCENT:
        exceeded.append(f"Disk at {metrics['disk']:.1f}% (threshold: {THRESHOLD_PERCENT}%)")
    return exceeded


# ─────────────────────────────────────────────
# GCP Scale-Up Function
# ─────────────────────────────────────────────

def trigger_gcp_scaleup(reason: str):
    """
    Calls gcloud CLI to increase the Managed Instance Group size by 1.
    Falls back to simulation mode if gcloud is not installed/authenticated.
    Also increments the Prometheus scale-up counter.
    """
    logger.warning(f"📡 Triggering GCP scale-up. Reason: {reason}")

    # Increment Prometheus counter — even in simulation mode
    if PROMETHEUS_AVAILABLE:
        PROM_SCALEUP.inc()

    # ── Get current MIG size ──────────────────────────────────────────────────
    try:
        describe_cmd = [
            "gcloud", "compute", "instance-groups", "managed", "describe",
            GCP_MIG_NAME,
            "--zone", GCP_ZONE,
            "--project", GCP_PROJECT_ID,
            "--format", "json"
        ]
        result = subprocess.run(describe_cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.error(f"❌ gcloud describe failed: {result.stderr.strip()}")
            _log_simulated_scaleup(reason)
            return

        mig_info     = json.loads(result.stdout)
        current_size = mig_info.get("targetSize", 1)
        logger.info(f"   Current VM count: {current_size}")

    except FileNotFoundError:
        logger.warning("⚠️  gcloud CLI not found — running in SIMULATION MODE.")
        _log_simulated_scaleup(reason)
        return
    except subprocess.TimeoutExpired:
        logger.error("❌ gcloud describe timed out after 30s.")
        return
    except json.JSONDecodeError:
        logger.error("❌ Could not parse gcloud output as JSON.")
        return

    # ── Resize the MIG ────────────────────────────────────────────────────────
    MAX_INSTANCES = 5
    new_size      = min(current_size + 1, MAX_INSTANCES)

    if new_size == current_size:
        logger.info(f"   Already at maximum ({MAX_INSTANCES} VMs). No scale-up needed.")
        return

    logger.info(f"   Scaling {current_size} → {new_size} VMs...")

    try:
        resize_cmd = [
            "gcloud", "compute", "instance-groups", "managed", "resize",
            GCP_MIG_NAME,
            "--size", str(new_size),
            "--zone", GCP_ZONE,
            "--project", GCP_PROJECT_ID,
            "--quiet"
        ]
        resize_result = subprocess.run(resize_cmd, capture_output=True, text=True, timeout=60)

        if resize_result.returncode == 0:
            logger.info(f"   ✅ Scale-up successful! Running VMs: {new_size}")
        else:
            logger.error(f"   ❌ Resize failed: {resize_result.stderr.strip()}")

    except subprocess.TimeoutExpired:
        logger.error("   ❌ Resize command timed out.")


def _log_simulated_scaleup(reason: str):
    """Logs a simulated scale-up for testing without real GCP access."""
    logger.info("=" * 60)
    logger.info("  [SIMULATION] GCP Scale-Up Triggered")
    logger.info(f"  Reason   : {reason}")
    logger.info(f"  Project  : {GCP_PROJECT_ID}")
    logger.info(f"  Zone     : {GCP_ZONE}")
    logger.info(f"  MIG Name : {GCP_MIG_NAME}")
    logger.info("  Action   : Would add 1 VM (up to max 5)")
    logger.info("  Note     : Install & auth gcloud for real scaling")
    logger.info("=" * 60)


# ─────────────────────────────────────────────
# Prometheus HTTP Server Starter
# ─────────────────────────────────────────────

def start_prometheus_server():
    """
    Starts the Prometheus metrics HTTP server in a background thread.
    Once started, Prometheus can scrape http://localhost:8000/metrics
    every 10 seconds (as configured in prometheus.yml).

    This is non-blocking — it runs in the background while the
    monitoring loop runs in the main thread.
    """
    if not PROMETHEUS_AVAILABLE:
        logger.warning("⚠️  Skipping Prometheus server (prometheus-client not installed).")
        return

    try:
        start_http_server(PROMETHEUS_PORT)
        logger.info(f"📊 Prometheus metrics server started → http://localhost:{PROMETHEUS_PORT}/metrics")
        logger.info(f"   Grafana can now scrape: vcc_cpu_percent, vcc_ram_percent, vcc_disk_percent, vcc_scaleup_total")
    except OSError as e:
        logger.warning(f"⚠️  Could not start Prometheus server on port {PROMETHEUS_PORT}: {e}")
        logger.warning("   Another process may already be using that port.")


# ─────────────────────────────────────────────
# Main Monitoring Loop
# ─────────────────────────────────────────────

def run_monitor():
    """
    Main loop:
    1. Collect CPU/RAM/Disk metrics (also updates Prometheus gauges)
    2. Check thresholds
    3. Log status
    4. If exceeded: alert + trigger GCP scale-up
    5. Wait CHECK_INTERVAL seconds
    6. Repeat forever (until Ctrl+C)
    """
    logger.info("=" * 65)
    logger.info("  VCC Assignment 3 — Resource Monitor + Prometheus Exporter")
    logger.info("=" * 65)
    logger.info(f"  Threshold   : {THRESHOLD_PERCENT}%")
    logger.info(f"  Check Rate  : every {CHECK_INTERVAL} seconds")
    logger.info(f"  Log File    : {os.path.abspath(LOG_FILE)}")
    logger.info(f"  GCP MIG     : {GCP_MIG_NAME} ({GCP_ZONE})")
    logger.info(f"  Prometheus  : http://localhost:{PROMETHEUS_PORT}/metrics")
    logger.info("=" * 65)
    logger.info("Press Ctrl+C to stop.\n")

    last_scaleup_time = 0
    SCALEUP_COOLDOWN  = 120   # Minimum seconds between scale-up triggers

    try:
        while True:
            # Collect metrics — this also updates Prometheus gauge values
            metrics = collect_metrics()

            status_line = (
                f"CPU: {metrics['cpu']:5.1f}% | "
                f"RAM: {metrics['ram']:5.1f}% | "
                f"Disk: {metrics['disk']:5.1f}%"
            )

            exceeded = check_thresholds(metrics)

            if exceeded:
                logger.warning(f"⚠️  THRESHOLD EXCEEDED — {status_line}")
                for v in exceeded:
                    logger.warning(f"   🚨 ALERT: {v}")

                now = time.time()
                if now - last_scaleup_time > SCALEUP_COOLDOWN:
                    trigger_gcp_scaleup(" | ".join(exceeded))
                    last_scaleup_time = now
                else:
                    remaining = int(SCALEUP_COOLDOWN - (now - last_scaleup_time))
                    logger.info(f"   ⏳ Cooldown active — {remaining}s until next scale-up trigger.")
            else:
                logger.info(f"✅ OK  — {status_line}")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("\n🛑 Monitor stopped (Ctrl+C). Goodbye!")


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Start Prometheus /metrics HTTP server in the background
    # This is a daemon thread — it stops automatically when the main program exits
    prom_thread = threading.Thread(target=start_prometheus_server, daemon=True)
    prom_thread.start()

    # Small delay to let the server start before the first log line
    time.sleep(0.5)

    # Run the main monitoring loop (blocks until Ctrl+C)
    run_monitor()
