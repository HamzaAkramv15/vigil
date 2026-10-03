"""Config, alerts and the 'What's wrong?' diagnosis. Pure functions over the snapshot."""
import json
import os
import shutil
import subprocess
import time

CONFIG_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "vigil", "config.json")

# id, label, comparison, default threshold, seconds it must persist, unit
RULES = [
    ("cpu", "CPU usage", ">", 90, 120, "%"),
    ("cpu_temp", "CPU temperature", ">", 90, 30, "°C"),
    ("gpu_temp", "GPU temperature", ">", 85, 10, "°C"),
    ("nvme_temp", "NVMe temperature", ">", 70, 30, "°C"),
    ("ram", "RAM usage", ">", 90, 60, "%"),
    ("swap", "Swap usage", ">", 50, 60, "%"),
    ("disk", "Disk usage", ">", 90, 0, "%"),
    ("batt_health", "Battery health below", "<", 70, 0, "%"),
]
COOLDOWN = 600  # seconds before the same alert can fire again


class Config:
    def __init__(self):
        self.data = {"background": False, "alerts": {}}
        try:
            with open(CONFIG_PATH) as f:
                self.data.update(json.load(f))
        except Exception:
            pass

    def alert(self, rid):
        default = next(r for r in RULES if r[0] == rid)
        a = self.data["alerts"].get(rid, {})
        return a.get("on", True), a.get("value", default[3])

    def set_alert(self, rid, on=None, value=None):
        a = self.data["alerts"].setdefault(rid, {})
        if on is not None:
            a["on"] = on
        if value is not None:
            a["value"] = value
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass


def metric(snap, rid):
    b = snap.get("battery")
    return {
        "cpu": snap.get("cpu"), "cpu_temp": snap.get("cpu_temp"), "gpu_temp": snap.get("gpu_temp"),
        "nvme_temp": snap.get("nvme_temp"), "ram": snap.get("ram", {}).get("pct"),
        "swap": snap.get("swap", {}).get("pct") if snap.get("swap", {}).get("total") else None,
        "disk": snap.get("disk_pct"), "batt_health": b.get("health") if b else None,
    }.get(rid)


class AlertEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.since = {}
        self.last_fired = {}
        self.log = []  # (timestamp, text)

    def check(self, snap):
        now = time.time()
        for rid, label, cmp_, _default, secs, unit in RULES:
            on, limit = self.cfg.alert(rid)
            v = metric(snap, rid)
            if not on or v is None:
                self.since.pop(rid, None)
                continue
            hit = v > limit if cmp_ == ">" else v < limit
            if not hit:
                self.since.pop(rid, None)
                continue
            start = self.since.setdefault(rid, now)
            if now - start >= secs and now - self.last_fired.get(rid, 0) >= COOLDOWN:
                self.last_fired[rid] = now
                text = f"{label} {'reached' if cmp_ == '>' else 'fell to'} {v:.0f}{unit} (limit {limit}{unit})"
                self.log.append((now, text))
                self.log = self.log[-100:]
                self._notify(text)

    @staticmethod
    def _notify(text):
        if shutil.which("notify-send"):
            try:
                subprocess.Popen(["notify-send", "-a", "Vigil", "-i", "vigil", "⚠ Vigil", text])
            except OSError:
                pass


def diagnose(snap):
    """Return [(level, text)] where level is 'warn' | 'ok' | 'info'. Surfaces anomalies, decides nothing."""
    out = []
    if not snap:
        return [("info", "Collecting data…")]
    ram, swap = snap["ram"], snap["swap"]
    mem_p = snap["psi"].get("memory")
    if (mem_p is not None and mem_p > 10) or ram["available"] < ram["total"] * 0.08:
        out.append(("warn", "High memory pressure" + (f" (stalled {mem_p:.0f}% of the last 10 s)" if mem_p else "")))
    else:
        out.append(("ok", "Memory pressure normal"))
    if swap["total"] and swap["pct"] > 50:
        out.append(("warn", f"Swap is {swap['pct']:.0f}% used"))

    n = snap["cpu_count"]
    per = snap["cpu_per"]
    if per and max(per) > 95 and snap["cpu"] < 35:
        out.append(("warn", f"One core is pinned (CPU {per.index(max(per))}) while the rest are idle"))
    if snap["load"][0] > n * 1.5:
        out.append(("warn", f"Load average {snap['load'][0]:.1f} is high for {n} threads"))
    ct = snap["cpu_temp"]
    if ct is not None:
        out.append(("warn", f"CPU temperature is {ct:.0f}°C") if ct > 85 else ("ok", f"CPU temperature normal ({ct:.0f}°C)"))
    io_p = snap["psi"].get("io")
    if io_p is not None and io_p > 20:
        out.append(("warn", f"Disk I/O is stalling tasks ({io_p:.0f}% of the last 10 s)"))

    b = snap.get("battery")
    for g in snap["gpus"]:
        if g["kind"] == "dGPU" and g["state"] not in ("suspended",) and b and not b["ac"] and b["status"] == "Discharging":
            out.append(("warn", f"{g['name']} is awake while on battery"))
        if g["temp"] is not None and g["temp"] > 85:
            out.append(("warn", f"{g['kind']} temperature is {g['temp']:.0f}°C"))
    if snap.get("nvme_temp") and snap["nvme_temp"] > 70:
        out.append(("warn", f"NVMe temperature is {snap['nvme_temp']:.0f}°C"))

    for m in snap["mounts"]:
        if m["pct"] > 90:
            out.append(("warn", f"{m['mount']} filesystem is {m['pct']:.0f}% full"))
    if b and b["health"] is not None and b["health"] < 80:
        out.append(("warn", f"Battery health is {b['health']:.0f}% of design capacity"))

    failed = snap.get("failed_services") or []
    if failed:
        out.append(("warn", f"{len(failed)} failed systemd service(s): " + ", ".join(failed[:4])))
    else:
        out.append(("ok", "No failed systemd services"))

    top = max(snap["procs"], key=lambda r: r[3], default=None)
    if top and top[3] > 80:
        out.append(("info", f"{top[1]} is using {top[3]:.0f}% of a core"))

    if not any(l == "warn" for l, _ in out):
        out.insert(0, ("ok", "Nothing unusual"))
    out.sort(key=lambda x: {"warn": 0, "info": 1, "ok": 2}[x[0]])
    return out
