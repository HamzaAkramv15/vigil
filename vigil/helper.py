"""Vigil privileged helper. Started on request via pkexec; prints one JSON object per second.

It only *reads*: RAPL energy, /proc/<pid>/io of every user, an AF_PACKET byte counter that is
attributed to processes via /proc/net/* socket inodes (like nethogs), and intel_gpu_top output.
It exits as soon as its stdin closes (i.e. when Vigil exits).
"""
import glob
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict

RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"
lock = threading.Lock()
port_bytes = defaultdict(lambda: [0, 0])   # (proto, local_port) -> [down, up]
igpu = {}


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


# ------------------------------------------------------------------ net
def sniff():
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.ntohs(3))
    except OSError as e:
        print(json.dumps({"error": f"packet socket: {e}"}), flush=True)
        return
    s.settimeout(1.0)
    while True:
        try:
            data, addr = s.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            return
        ifname, proto, pkttype = addr[0], addr[1], addr[2]
        if ifname == "lo" or pkttype not in (0, 4) or len(data) < 24:
            continue
        try:
            if proto == 0x0800:
                ihl = (data[0] & 0x0F) * 4
                ipp, length, off = data[9], struct.unpack("!H", data[2:4])[0], ihl
            elif proto == 0x86DD:
                ipp, length, off = data[6], 40 + struct.unpack("!H", data[4:6])[0], 40
            else:
                continue
            if ipp not in (6, 17) or len(data) < off + 4:
                continue
            sport, dport = struct.unpack("!HH", data[off:off + 4])
        except Exception:
            continue
        out = pkttype == 4
        with lock:
            port_bytes[(ipp, sport if out else dport)][1 if out else 0] += length


def socket_table():
    """(proto, port) -> inode"""
    tbl = {}
    for proto, names in ((6, ("tcp", "tcp6")), (17, ("udp", "udp6"))):
        for n in names:
            try:
                with open("/proc/net/" + n) as f:
                    next(f)
                    for line in f:
                        p = line.split()
                        inode = int(p[9])
                        if inode:
                            tbl[(proto, int(p[1].rsplit(":", 1)[1], 16))] = inode
            except Exception:
                pass
    return tbl


def inode_pids():
    m = {}
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = int(fd_dir.split("/")[2])
        try:
            for fd in os.listdir(fd_dir):
                try:
                    t = os.readlink(f"{fd_dir}/{fd}")
                except OSError:
                    continue
                if t.startswith("socket:["):
                    m[int(t[8:-1])] = pid
        except OSError:
            continue
    return m


# ------------------------------------------------------------------- gpu
def intel_gpu():
    exe = shutil.which("intel_gpu_top")
    if not exe:
        return
    p = subprocess.Popen([exe, "-J", "-s", "1000"], stdout=subprocess.PIPE, text=True)
    dec, buf = json.JSONDecoder(), ""
    for chunk in iter(lambda: p.stdout.read(512), ""):
        buf += chunk
        while True:
            buf = buf.lstrip(" \n,[")
            try:
                obj, end = dec.raw_decode(buf)
            except ValueError:
                break
            buf = buf[end:]
            eng = obj.get("engines", {})
            busy = [e.get("busy", 0) for e in eng.values()]
            igpu.update(util=max(busy) if busy else 0.0, power=obj.get("power", {}).get("GPU"))


def all_io():
    out = {}
    for d in glob.glob("/proc/[0-9]*"):
        try:
            with open(d + "/io") as f:
                v = dict(l.split(": ") for l in f.read().splitlines() if ": " in l)
            out[int(d[6:])] = int(v["read_bytes"]) + int(v["write_bytes"])
        except Exception:
            continue
    return out


def main():
    if os.geteuid() != 0:
        print(json.dumps({"error": "not root"}), flush=True)
        return 1

    def watch_parent():  # Vigil closes our stdin when it exits
        sys.stdin.read()
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    threading.Thread(target=sniff, daemon=True).start()
    threading.Thread(target=intel_gpu, daemon=True).start()

    last_rapl, last_t = read_int(RAPL), time.monotonic()
    tbl, pids, tbl_t = {}, {}, 0.0
    while True:
        time.sleep(1.0)
        now = time.monotonic()
        dt, last_t = now - last_t, now
        out = {"t": time.time()}
        e = read_int(RAPL)
        if e is not None and last_rapl is not None and e >= last_rapl:
            out["rapl_w"] = (e - last_rapl) / 1e6 / dt
        last_rapl = e

        with lock:
            snap = dict(port_bytes)
            port_bytes.clear()
        if snap:
            if now - tbl_t > 2.0 or any(k not in tbl for k in snap):
                tbl, tbl_t = socket_table(), now
                pids = inode_pids()
            net = defaultdict(lambda: [0.0, 0.0])
            for key, (d, u) in snap.items():
                pid = pids.get(tbl.get(key), 0)
                net[pid][0] += d / dt
                net[pid][1] += u / dt
            out["net"] = {str(p): v for p, v in net.items()}
        else:
            out["net"] = {}
        out["io"] = {str(p): v for p, v in all_io().items()}
        if igpu:
            out["igpu"] = dict(igpu)
        try:
            print(json.dumps(out), flush=True)
        except BrokenPipeError:
            return 0


if __name__ == "__main__":
    sys.exit(main())
