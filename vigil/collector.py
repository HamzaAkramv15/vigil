"""Vigil data collection.

A single background thread samples the system and publishes a snapshot dict
plus ring-buffer history. The UI only ever reads; it never touches /proc itself
(apart from the one selected process in the details panel).
"""
import glob
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import defaultdict, deque

import psutil

from .diagnose import AlertEngine, Config

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "vigil")
HIST_PATH = os.path.join(STATE_DIR, "history.json")
PCI = "/sys/bus/pci/devices"
PS = "/sys/class/power_supply"


def read(path, default=None, cast=str):
    try:
        with open(path) as f:
            return cast(f.read().strip())
    except Exception:
        return default


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class History:
    """1 s resolution for the last hour, 1 min averages for the last 7 days."""

    def __init__(self):
        self.fast = deque(maxlen=3600)
        self.slow = deque(maxlen=10080)
        self._acc = []
        self._lock = threading.Lock()

    def add(self, v, times=1):
        with self._lock:
            for _ in range(times):
                self.fast.append(v)
                self._acc.append(v)
                if len(self._acc) >= 60:
                    vals = [x for x in self._acc if x is not None]
                    self.slow.append(sum(vals) / len(vals) if vals else None)
                    self._acc = []

    def get(self, seconds):
        with self._lock:
            if seconds <= 3600:
                return list(self.fast)[-seconds:]
            return list(self.slow)[-(seconds // 60):]

    def slots(self, seconds):
        return seconds if seconds <= 3600 else seconds // 60

    def dump(self):
        r = lambda seq: [None if v is None else round(v, 2) for v in seq]
        with self._lock:
            return {"f": r(self.fast), "s": r(self.slow)}

    def restore(self, d, gap):
        """Reload saved data and insert a gap of None for the time Vigil wasn't running."""
        with self._lock:
            fast, slow = list(d.get("f", [])), list(d.get("s", []))
            if gap < 3600:
                fast += [None] * int(gap)
            else:
                fast = []
            slow += [None] * min(int(gap // 60), 10080)
            self.fast.extend(fast[-3600:])
            self.slow.extend(slow[-10080:])


class HelperClient:
    """Talks to the opt-in root helper (vigil.helper) started through pkexec."""

    def __init__(self):
        self.proc, self.data, self.t, self.error = None, None, 0.0, None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def active(self):
        return self.running() and time.time() - self.t < 5 and self.data is not None

    def start(self):
        if self.running():
            return
        self.data, self.error = None, None
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            self.proc = subprocess.Popen(
                ["pkexec", "/usr/bin/env", f"PYTHONPATH={root}", "/usr/bin/python3", "-m", "vigil.helper"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError as e:
            self.error = str(e)
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "error" in d:
                self.error = d["error"]
                continue
            d["net"] = {int(k): v for k, v in d.get("net", {}).items()}
            d["io"] = {int(k): v for k, v in d.get("io", {}).items()}
            self.data, self.t = d, time.time()
        rc = proc.wait()
        if rc in (126, 127) and not self.error:
            self.error = "authorization cancelled"
        elif not self.error and self.data is None:
            self.error = f"helper exited ({rc})"

    def stop(self):
        if self.proc:
            try:
                self.proc.stdin.close()   # helper exits when stdin closes
            except Exception:
                pass
            self.proc = None
            self.data = None

    def latest(self):
        return self.data if self.active() else None


class Collector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="vigil-collector")
        self.cfg = Config()
        self.alerts = AlertEngine(self.cfg)
        self.hist = defaultdict(History)
        self.snap = {}
        self.visible = True       # window shown -> 1 s sampling + processes
        self.proc_every = 2       # seconds between full process scans
        self._halt = threading.Event()
        self._prev = {}
        self._t_prev = None
        self._tick = 0
        self._uids = {}
        self._pio = {}
        self._procs = []
        self._gpu_procs = {}
        self._last_proc_t = 0.0
        self._mounts = (0, [])
        self._failed = (0, [])
        self._conn = (0, {})
        self._rapl_path = next(iter(glob.glob("/sys/class/powercap/intel-rapl:0/energy_uj")), None)
        self.helper = HelperClient()
        self._net_names = {}
        self._last_save = time.monotonic()
        self._load_history()
        self.gpus = self._detect_gpus()
        self.has_smi = shutil.which("nvidia-smi") is not None
        self.sysinfo = self._sysinfo()

    # ------------------------------------------------------------------ run
    def stop(self):
        self._halt.set()
        self.save_history()
        self.helper.stop()

    # ------------------------------------------------------- persistence
    def save_history(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = HIST_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"t": time.time(), "keys": {k: h.dump() for k, h in list(self.hist.items())}}, f)
            os.replace(tmp, HIST_PATH)
        except Exception:
            pass

    def _load_history(self):
        try:
            with open(HIST_PATH) as f:
                d = json.load(f)
            gap = max(time.time() - d["t"], 0)
            for k, v in d["keys"].items():
                self.hist[k].restore(v, gap)
        except Exception:
            pass

    def run(self):
        psutil.cpu_percent(percpu=True)
        self._t_prev = time.monotonic()
        while not self._halt.is_set():
            t0 = time.monotonic()
            dt = max(t0 - self._t_prev, 0.05)
            self._t_prev = t0
            try:
                self._sample(dt)
            except Exception:
                traceback.print_exc()
            interval = 1.0 if self.visible else 5.0
            self._halt.wait(max(0.05, interval - (time.monotonic() - t0)))

    def _push(self, key, value, times):
        self.hist[key].add(value, times)

    # --------------------------------------------------------------- sample
    def _sample(self, dt):
        self._tick += 1
        times = max(1, round(dt))
        s = {"t": time.time(), "dt": dt}

        # CPU
        per = psutil.cpu_percent(percpu=True)
        s["cpu_per"] = per
        s["cpu"] = sum(per) / max(len(per), 1)
        freq = psutil.cpu_freq()
        s["cpu_freq"] = freq.current if freq else None
        s["load"] = os.getloadavg()
        s["cpu_count"] = len(per)
        temps = self._temps()
        s["temps"] = temps
        s["fans"] = self._fans()
        s["cpu_temp"] = self._cpu_temp(temps)
        s["nvme_temp"] = self._nvme_temp(temps)
        hd = self.helper.latest()
        s["helper"] = {"active": hd is not None, "error": self.helper.error, "running": self.helper.running()}
        s["cpu_power"] = self._rapl(dt)
        if s["cpu_power"] is None and hd:
            s["cpu_power"] = hd.get("rapl_w")
        s["psi"] = {n: self._psi(n) for n in ("cpu", "memory", "io")}

        # memory
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        s["ram"] = {
            "total": vm.total, "used": vm.total - vm.available, "available": vm.available,
            "cached": getattr(vm, "cached", 0), "buffers": getattr(vm, "buffers", 0),
            "pct": (vm.total - vm.available) / vm.total * 100 if vm.total else 0,
        }
        s["swap"] = {"total": sw.total, "used": sw.used, "pct": sw.percent if sw.total else 0.0}

        # disk + net rates
        s["disk"] = self._disk_rates(dt)
        s["net"] = self._net_rates(dt)
        s["mounts"] = self._mount_list()
        s["disk_pct"] = max([m["pct"] for m in s["mounts"]], default=None)

        # battery, GPU
        s["battery"] = self._battery()
        s["gpus"] = self._gpu_sample(hd)

        # processes (not while hidden; slower when not on the processes page)
        if self.visible and time.monotonic() - self._last_proc_t >= self.proc_every - 0.2:
            self._last_proc_t = time.monotonic()
            self._procs = self._scan_procs()
        s["procs"] = self._procs
        s["gpu_procs"] = self._gpu_procs
        s["net_top"] = self._net_top(hd)
        s["failed_services"] = self._failed_services()

        # history
        h = self._push
        h("cpu", s["cpu"], times)
        h("ram", s["ram"]["pct"], times)
        h("swap", s["swap"]["pct"], times)
        h("cpu_temp", s["cpu_temp"], times)
        h("disk_pct", s["disk_pct"], times)
        h("net_down", s["net"]["down"], times)
        h("net_up", s["net"]["up"], times)
        h("disk_r", s["disk"]["read"], times)
        h("disk_w", s["disk"]["write"], times)
        b = s["battery"]
        h("batt_pct", b["pct"] if b else None, times)
        h("power", b["power_w"] if b else None, times)
        for g in s["gpus"]:
            h(g["id"] + "_util", g["util"], times)
            h(g["id"] + "_temp", g["temp"], times)
        s["gpu_temp"] = max([g["temp"] for g in s["gpus"] if g["temp"] is not None], default=None)
        h("gpu_temp", s["gpu_temp"], times)

        self.snap = s
        self.alerts.check(s)
        if time.monotonic() - self._last_save > 600:
            self._last_save = time.monotonic()
            self.save_history()

    # -------------------------------------------------------------- sensors
    def _temps(self):
        try:
            return {k: [(e.label or k, e.current) for e in v] for k, v in psutil.sensors_temperatures().items()}
        except Exception:
            return {}

    def _fans(self):
        try:
            return {k: [(e.label or k, e.current) for e in v] for k, v in psutil.sensors_fans().items()}
        except Exception:
            return {}

    @staticmethod
    def _cpu_temp(temps):
        for chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            ents = temps.get(chip)
            if not ents:
                continue
            for label, v in ents:
                if label.startswith(("Package", "Tctl", "Tdie")):
                    return v
            return max(v for _, v in ents)
        return None

    @staticmethod
    def _nvme_temp(temps):
        vals = [v for l, v in temps.get("nvme", []) if l.startswith("Composite")] or \
               [v for _, v in temps.get("nvme", [])]
        return max(vals) if vals else None

    def _rapl(self, dt):
        if not self._rapl_path:
            return None
        e = read(self._rapl_path, None, int)
        if e is None:
            return None
        prev = self._prev.get("rapl")
        self._prev["rapl"] = e
        if prev is None or e < prev:
            return None
        return (e - prev) / 1e6 / dt

    @staticmethod
    def _psi(name):
        txt = read("/proc/pressure/" + name, "")
        m = re.search(r"some avg10=([\d.]+)", txt)
        return float(m.group(1)) if m else None

    # ------------------------------------------------------------ disk/net
    def _disk_rates(self, dt):
        c = psutil.disk_io_counters()
        out = {"read": 0.0, "write": 0.0, "riops": 0.0, "wiops": 0.0}
        if c is None:
            return out
        p = self._prev.get("disk")
        self._prev["disk"] = c
        if p:
            out = {"read": (c.read_bytes - p.read_bytes) / dt, "write": (c.write_bytes - p.write_bytes) / dt,
                   "riops": (c.read_count - p.read_count) / dt, "wiops": (c.write_count - p.write_count) / dt}
        return out

    def _net_rates(self, dt):
        cur = psutil.net_io_counters(pernic=True)
        prev = self._prev.get("net", {})
        self._prev["net"] = cur
        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
        ifs, down, up, pps = [], 0.0, 0.0, 0.0
        for name, c in cur.items():
            p = prev.get(name)
            d = (c.bytes_recv - p.bytes_recv) / dt if p else 0.0
            u = (c.bytes_sent - p.bytes_sent) / dt if p else 0.0
            pk = ((c.packets_recv - p.packets_recv) + (c.packets_sent - p.packets_sent)) / dt if p else 0.0
            if name != "lo":
                down, up, pps = down + d, up + u, pps + pk
            ip = next((a.address for a in addrs.get(name, []) if a.family.name == "AF_INET"), "")
            st = stats.get(name)
            ifs.append({"name": name, "up": bool(st and st.isup), "ip": ip, "down": d, "up_rate": u,
                        "signal": self._wifi_signal(name)})
        ssid_t, ssids = self._conn
        if time.monotonic() - ssid_t > 10:
            self._conn = (time.monotonic(), self._active_connections())
        return {"down": down, "up": up, "pps": pps, "ifs": ifs, "conns": self._conn[1]}

    @staticmethod
    def _wifi_signal(iface):
        txt = read("/proc/net/wireless", "")
        for line in txt.splitlines():
            if line.strip().startswith(iface + ":"):
                parts = line.split()
                if len(parts) > 3:
                    return num(parts[3].rstrip("."))
        return None

    @staticmethod
    def _active_connections():
        try:
            out = subprocess.run(["nmcli", "-t", "-f", "DEVICE,NAME", "connection", "show", "--active"],
                                 capture_output=True, text=True, timeout=3).stdout
            return {l.split(":", 1)[0]: l.split(":", 1)[1] for l in out.splitlines() if ":" in l}
        except Exception:
            return {}

    def _mount_list(self):
        t, cached = self._mounts
        if time.monotonic() - t < 10 and cached:
            return cached
        out, seen = [], set()
        for p in psutil.disk_partitions(all=False):
            if p.device in seen or p.fstype in ("squashfs", "tmpfs", "overlay", "vfat") and p.mountpoint != "/boot/efi":
                continue
            if p.mountpoint.startswith(("/snap", "/boot/efi", "/var/snap")):
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except Exception:
                continue
            out.append({"dev": p.device, "mount": p.mountpoint, "fs": p.fstype,
                        "total": u.total, "used": u.used, "free": u.free, "pct": u.percent})
        self._mounts = (time.monotonic(), out)
        return out

    # ------------------------------------------------------------- battery
    def _battery(self):
        bats = sorted(glob.glob(PS + "/BAT*"))
        if not bats:
            return None
        b = bats[0]
        r = lambda n, c=str: read(f"{b}/{n}", None, c)
        volt = (r("voltage_now", int) or 0) / 1e6
        # energy_* is µWh; charge_* is µAh -> multiply by voltage for Wh
        def wh(name):
            v = r("energy_" + name, int)
            if v is not None:
                return v / 1e6
            c = r("charge_" + name, int)
            return c / 1e6 * (r("voltage_min_design", int) or r("voltage_now", int) or 0) / 1e6 if c is not None else None
        full, design, now = wh("full"), wh("full_design"), wh("now")
        power = r("power_now", int)
        power_w = power / 1e6 if power is not None else None
        if power_w is None:
            cur = r("current_now", int)
            if cur is not None and volt:
                power_w = cur / 1e6 * volt
        status = r("status") or "Unknown"
        ac = any(read(p + "/online") == "1" for p in glob.glob(PS + "/A*") + glob.glob(PS + "/ADP*"))
        eta = None
        if power_w and power_w > 0.5 and now is not None and full is not None:
            if status == "Discharging":
                eta = now / power_w * 3600
            elif status == "Charging":
                eta = (full - now) / power_w * 3600
        return {
            "name": os.path.basename(b), "pct": r("capacity", float), "status": status, "power_w": power_w, "voltage": volt or None,
            "full": full, "design": design, "now": now,
            "health": (full / design * 100) if full and design else None,
            "cycles": r("cycle_count", int), "limit": r("charge_control_end_threshold", int),
            "ac": ac, "eta": eta, "tech": r("technology"),
        }

    # ----------------------------------------------------------------- GPU
    def _detect_gpus(self):
        out = []
        for d in sorted(glob.glob(PCI + "/*")):
            cls = read(d + "/class", "")
            if not cls.startswith("0x03"):
                continue
            slot = os.path.basename(d)
            vendor = read(d + "/vendor", "")
            kind = "iGPU" if slot.split(":")[1] == "00" else "dGPU"  # integrated GPUs sit on PCI bus 00
            drm = glob.glob(d + "/drm/card[0-9]")
            out.append({"id": "gpu%d" % len(out), "slot": slot, "dev": d, "vendor": vendor,
                        "kind": kind, "name": self._pci_name(slot, vendor),
                        "card": drm[0] if drm else None})
        return out

    @staticmethod
    def _pci_name(slot, vendor):
        fallback = {"0x10de": "NVIDIA GPU", "0x1002": "AMD GPU", "0x8086": "Intel Graphics"}.get(vendor, "GPU")
        try:
            out = subprocess.run(["lspci", "-mm", "-s", slot], capture_output=True, text=True, timeout=3).stdout
            f = re.findall(r'"([^"]*)"', out)
            if len(f) >= 3:
                m = re.search(r"\[(.+)\]", f[2])
                return m.group(1) if m else f[2]
        except Exception:
            pass
        return fallback

    def _gpu_sample(self, hd=None):
        res, smi = [], None
        for g in self.gpus:
            d = g["dev"]
            row = {"id": g["id"], "kind": g["kind"], "name": g["name"], "state": "active", "util": None,
                   "vram_used": None, "vram_total": None, "temp": None, "clock": None, "power": None,
                   "fan": None, "enc": None, "dec": None}
            rt = read(d + "/power/runtime_status")
            if g["vendor"] == "0x10de":
                # Don't wake a sleeping dGPU just to look at it (hybrid laptops).
                if rt == "suspended":
                    row["state"] = "suspended"
                elif self.has_smi:
                    if smi is None:
                        smi = self._smi()
                    v = smi.get(g["slot"][-12:].lower())
                    if v:
                        row.update(v)
                    else:
                        row["state"] = "no data"
                else:
                    row["state"] = "no driver tools"
            else:
                row.update(self._sysfs_gpu(g))
                if g["vendor"] == "0x8086" and hd and hd.get("igpu"):
                    row["util"] = hd["igpu"].get("util")
                    row["power"] = hd["igpu"].get("power") or row["power"]
                if rt == "suspended":
                    row["state"] = "suspended"
            res.append(row)
        return res

    _SMI = "utilization.gpu,memory.used,memory.total,temperature.gpu,clocks.gr,power.draw,fan.speed," \
           "utilization.encoder,utilization.decoder,pstate"

    def _smi(self):
        out = {}
        try:
            txt = subprocess.run(["nvidia-smi", "--query-gpu=pci.bus_id," + self._SMI,
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4).stdout
            for line in txt.splitlines():
                f = [x.strip() for x in line.split(",")]
                if len(f) < 11:
                    continue
                mib = 1024 * 1024
                out[f[0][-12:].lower()] = {
                    "util": num(f[1]), "vram_used": (num(f[2]) or 0) * mib, "vram_total": (num(f[3]) or 0) * mib,
                    "temp": num(f[4]), "clock": num(f[5]), "power": num(f[6]), "fan": num(f[7]),
                    "enc": num(f[8]), "dec": num(f[9]), "state": f[10] or "active"}
            if any(v["util"] for v in out.values()) and self._tick % 2 == 0:
                self._gpu_procs = self._smi_procs()
            elif not any(v["util"] for v in out.values()):
                self._gpu_procs = {}
        except Exception:
            pass
        return out

    @staticmethod
    def _smi_procs():
        try:
            txt = subprocess.run(["nvidia-smi", "pmon", "-c", "1"], capture_output=True, text=True, timeout=4).stdout
            res = {}
            for line in txt.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                f = line.split()
                if len(f) >= 4 and f[1].isdigit():
                    res[int(f[1])] = num(f[3]) or 0.0
            return res
        except Exception:
            return {}

    @staticmethod
    def _sysfs_gpu(g):
        d, o = g["dev"], {}
        busy = read(d + "/gpu_busy_percent", None, float)
        if busy is not None:
            o["util"] = busy
        vu, vt = read(d + "/mem_info_vram_used", None, int), read(d + "/mem_info_vram_total", None, int)
        if vt:
            o["vram_used"], o["vram_total"] = vu, vt
        hw = glob.glob(d + "/hwmon/hwmon*")
        if hw:
            t = read(hw[0] + "/temp1_input", None, float)
            o["temp"] = t / 1000 if t is not None else None
            p = read(hw[0] + "/power1_average", None, float) or read(hw[0] + "/power1_input", None, float)
            o["power"] = p / 1e6 if p else None
            o["fan"] = read(hw[0] + "/fan1_input", None, float)
        clk = None
        m = re.search(r"(\d+)Mhz \*", read(d + "/pp_dpm_sclk", "") or "")
        if m:
            clk = float(m.group(1))
        elif g["card"]:
            clk = read(g["card"] + "/gt_cur_freq_mhz", None, float)
        o["clock"] = clk
        return o

    # ----------------------------------------------------------- processes
    def _user(self, uid):
        if uid not in self._uids:
            try:
                self._uids[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                self._uids[uid] = str(uid)
        return self._uids[uid]

    def _scan_procs(self):
        now = time.monotonic()
        dt = max(now - self._prev.get("proc_t", now - 2), 0.1)
        self._prev["proc_t"] = now
        gpu = self._gpu_procs
        hd = self.helper.latest() or {}
        hio, hnet = hd.get("io", {}), hd.get("net", {})
        rows, newio = [], {}
        for p in psutil.process_iter():
            try:
                with p.oneshot():
                    pid = p.pid
                    cpu = p.cpu_percent()
                    mem = p.memory_info().rss
                    io = None
                    try:
                        c = p.io_counters()
                        io = c.read_bytes + c.write_bytes
                    except (psutil.AccessDenied, AttributeError):
                        io = hio.get(pid)
                    disk = 0.0
                    if io is not None:
                        newio[pid] = io
                        if pid in self._pio:
                            disk = max(io - self._pio[pid], 0) / dt
                    rows.append((pid, p.name(), self._user(p.uids().real), cpu, mem, gpu.get(pid, 0.0), disk,
                                 p.num_threads(), p.status(), p.ppid(), p.create_time(), sum(hnet.get(pid, (0, 0)))))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self._pio = newio
        return rows

    def _net_top(self, hd):
        """Aggregate per-process network rates by process name: [(name, down, up)], busiest first."""
        if not hd:
            return []
        names = {r[0]: r[1] for r in self._procs}
        agg = {}
        for pid, (d, u) in hd.get("net", {}).items():
            n = names.get(pid, "kernel / unknown" if pid == 0 else f"pid {pid}")
            a = agg.setdefault(n, [0.0, 0.0])
            a[0] += d
            a[1] += u
        return sorted(((n, d, u) for n, (d, u) in agg.items() if d + u >= 1), key=lambda x: -(x[1] + x[2]))[:12]

    # ------------------------------------------------------------ services
    def _failed_services(self):
        t, cached = self._failed
        if time.monotonic() - t < 60:
            return cached
        names = []
        try:
            out = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
                                 capture_output=True, text=True, timeout=5).stdout
            names = [l.split()[0] for l in out.splitlines() if l.strip()]
        except Exception:
            pass
        self._failed = (time.monotonic(), names)
        return names

    # ----------------------------------------------------------- system info
    def _sysinfo(self):
        osname = "Linux"
        for line in (read("/etc/os-release", "") or "").splitlines():
            if line.startswith("PRETTY_NAME="):
                osname = line.split("=", 1)[1].strip('"')
        cpu = next((l.split(":", 1)[1].strip() for l in (read("/proc/cpuinfo", "") or "").splitlines()
                    if l.startswith("model name")), "Unknown CPU")
        root = psutil.disk_usage("/")
        return {
            "OS": osname, "Kernel": os.uname().release, "Hostname": os.uname().nodename,
            "Desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
            "Session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            "CPU": f"{cpu} ({psutil.cpu_count(logical=False) or '?'}C / {psutil.cpu_count()}T)",
            "GPU": ", ".join(f"{g['kind']}: {g['name']}" for g in self.gpus) or "none detected",
            "RAM": f"{psutil.virtual_memory().total / 2**30:.1f} GiB",
            "Storage (/)": f"{root.total / 2**30:.0f} GiB",
        }
