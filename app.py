"""
app.py — VCC Assignment 3: Sample Flask Application
=====================================================
Author: Ashish
Course: Virtual Cloud Computing (VCC) — Assignment 3

Description:
    A simple Flask web application that:
    - Serves a health-check endpoint at GET /
    - Returns live resource stats as JSON at GET /status
    - Simulates heavy CPU load at GET /stress (for 10 seconds)
      so you can test the resource monitor's threshold detection

Usage:
    # Install dependencies first:
    pip install -r requirements.txt

    # Run the app:
    python3 app.py

    # Then open in your browser:
    # http://localhost:5000/          → health check
    # http://localhost:5000/status    → resource usage JSON
    # http://localhost:5000/stress    → trigger high CPU for 10s
"""

import time
import math
import threading
import psutil

from flask import Flask, jsonify


# ─────────────────────────────────────────────
# Flask App Initialization
# ─────────────────────────────────────────────
# Flask(__name__) creates the web application.
# __name__ is a Python special variable that equals the name of the current
# module — Flask uses it to know where to find templates and static files.

app = Flask(__name__)

# How long the /stress endpoint should burn CPU (seconds)
STRESS_DURATION_SECONDS = 10


# ─────────────────────────────────────────────
# Route Definitions
# ─────────────────────────────────────────────

@app.route("/")
def index():
    """
    GET /
    ------
    Simple health-check endpoint.
    Returns a plain-text message confirming the app is running.

    Test it: curl http://localhost:5000/
    """
    return (
        "<h2>✅ VCC Assignment 3 — Flask App is Running!</h2>"
        "<p>Available endpoints:</p>"
        "<ul>"
        "  <li><a href='/status'>/status</a> — Current resource usage (JSON)</li>"
        "  <li><a href='/stress'>/stress</a> — Trigger 10-second CPU stress test</li>"
        "</ul>"
    )


@app.route("/status")
def status():
    """
    GET /status
    -----------
    Returns current CPU, RAM, and Disk usage as a JSON response.
    The resource monitor script (monitor.py) independently reads
    these same metrics directly from the OS via psutil.

    Example response:
    {
        "cpu_percent": 45.2,
        "ram_percent": 67.8,
        "disk_percent": 24.1,
        "ram_total_gb": 3.9,
        "ram_used_gb": 2.6,
        "disk_total_gb": 19.8,
        "disk_used_gb": 4.7,
        "status": "ok"
    }

    Test it: curl http://localhost:5000/status
    """
    # CPU usage over a 1-second interval for accuracy
    cpu_percent  = psutil.cpu_percent(interval=1)

    # RAM details
    ram           = psutil.virtual_memory()
    ram_percent   = ram.percent
    ram_total_gb  = round(ram.total / (1024 ** 3), 2)     # bytes → GB
    ram_used_gb   = round(ram.used  / (1024 ** 3), 2)

    # Disk details for root partition
    disk          = psutil.disk_usage("/")
    disk_percent  = disk.percent
    disk_total_gb = round(disk.total / (1024 ** 3), 2)
    disk_used_gb  = round(disk.used  / (1024 ** 3), 2)

    # Determine overall status
    threshold  = 75.0
    any_high   = cpu_percent > threshold or ram_percent > threshold or disk_percent > threshold
    app_status = "warning — threshold exceeded!" if any_high else "ok"

    return jsonify({
        "cpu_percent":    cpu_percent,
        "ram_percent":    ram_percent,
        "ram_total_gb":   ram_total_gb,
        "ram_used_gb":    ram_used_gb,
        "disk_percent":   disk_percent,
        "disk_total_gb":  disk_total_gb,
        "disk_used_gb":   disk_used_gb,
        "threshold":      threshold,
        "status":         app_status
    })


@app.route("/stress")
def stress():
    """
    GET /stress
    -----------
    Deliberately burns CPU for STRESS_DURATION_SECONDS seconds.
    This simulates a computationally heavy workload so that the
    resource monitor (monitor.py) detects high CPU and triggers
    the GCP scale-up alert.

    How it works:
    - We spin up a background thread that runs a tight math loop.
    - The main thread waits for the stress to finish, then returns
      a summary of what happened.

    IMPORTANT: Visiting this endpoint WILL make your VM feel sluggish
    for ~10 seconds. That is the point — it's a stress TEST.

    Test it: curl http://localhost:5000/stress
             (or just open it in your browser)
    """
    start_time = time.time()

    # Capture metrics BEFORE the stress so we can compare
    cpu_before  = psutil.cpu_percent(interval=0.5)
    ram_before  = psutil.virtual_memory().percent

    # ── Run the stress in a background thread ────────────────────────────────
    # We use a thread so Flask can still respond when the stress finishes.
    # If we blocked the main thread, the HTTP response couldn't be sent.
    stress_complete = threading.Event()

    def cpu_burn():
        """Inner function: does heavy math to burn CPU."""
        end_at = time.time() + STRESS_DURATION_SECONDS
        while time.time() < end_at:
            # math.sqrt and math.sin are CPU-intensive enough to push usage up
            # without using any external libraries
            _ = math.sqrt(sum(math.sin(i) * math.cos(i) for i in range(10_000)))
        stress_complete.set()   # Signal that we're done

    burn_thread = threading.Thread(target=cpu_burn, daemon=True)
    burn_thread.start()

    # Wait for the stress to complete (blocks this request handler for ~10s)
    stress_complete.wait(timeout=STRESS_DURATION_SECONDS + 5)

    elapsed    = round(time.time() - start_time, 1)
    cpu_after  = psutil.cpu_percent(interval=0.5)
    ram_after  = psutil.virtual_memory().percent

    return jsonify({
        "message":              f"CPU stress test completed in {elapsed}s",
        "duration_seconds":     STRESS_DURATION_SECONDS,
        "cpu_before_percent":   cpu_before,
        "cpu_after_percent":    cpu_after,
        "ram_before_percent":   ram_before,
        "ram_after_percent":    ram_after,
        "tip": (
            "If monitor.py is running, check its output — it should have "
            "logged an alert and triggered the GCP scale-up function!"
        )
    })


# ─────────────────────────────────────────────
# 404 Error Handler
# ─────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    """Return a helpful JSON error instead of Flask's default HTML 404 page."""
    return jsonify({
        "error":     "404 Not Found",
        "message":   "That endpoint doesn't exist.",
        "endpoints": ["/", "/status", "/stress"]
    }), 404


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  VCC Assignment 3 — Flask App Starting")
    print("=" * 55)
    print("  Health Check : http://localhost:5000/")
    print("  Resource Stats: http://localhost:5000/status")
    print("  Stress Test  : http://localhost:5000/stress")
    print("=" * 55)
    print()

    # host="0.0.0.0" means Flask listens on ALL network interfaces,
    # not just localhost. This is required so traffic forwarded from
    # VirtualBox's port-forwarding rule (host 5000 → guest 5000) reaches Flask.
    #
    # debug=True enables auto-reload when you edit app.py and shows
    # detailed error pages. Turn it OFF in production.
    app.run(host="0.0.0.0", port=5000, debug=True)
