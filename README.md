# Vigil

A dark-purple system monitor built as a visual identity piece for **Ubuntu Unity** (works on any GTK3 desktop). Open it, understand your machine in three seconds, dig deeper when something looks wrong.

- **Overview**: CPU / Memory / Swap / Disk cards with sparklines, health, temperature, every GPU (iGPU and dGPU kept separate), battery, CPU graph, top processes (grouped per app, with icons), memory/swap donuts, network graph, disk usage and file systems
- **Processes**: search, sort any column, End / Kill / Stop / Continue (`pkexec` fallback for other users' processes), per-process CPU, RAM, GPU, disk, **network**, details panel
- **Resources**: CPU (per-core, "one core pinned?" verdict), Memory (pressure, cache-aware), GPU, Sensors (deduplicated), Battery (health, cycles, **charge limit control**)
- **File Systems / Network**: mounts, throughput, IOPS, NVMe temperature, interfaces, Wi-Fi signal, per-process network
- **History**: 1 min → 10 min → 1 h → 24 h → 7 days, **kept across restarts**
- **What's wrong?**: flags anomalies (memory pressure, pinned core, dGPU awake on battery, nearly-full disks, failed services…)
- **Alerts**: desktop notifications with editable thresholds; **start at login** + **keep running in background** so they always fire
- **Panel indicator**: `12% · 48°C` next to the Unity panel icons (needs `gir1.2-ayatanaappindicator3-0.1`)
- **Services**: systemd units, failed first, start/stop/restart/enable/disable via polkit
- **Lightweight**: 5 s sampling and no process scans while hidden; a sleeping NVIDIA dGPU is never woken to read stats

### Privileged data (opt-in)
One click (**Enable privileged data**, polkit password prompt) starts a small read-only root helper that unlocks:
per-process **network** (packet counters attributed to processes, like nethogs), per-process **disk I/O for all users**, **Intel iGPU load** (via `intel-gpu-tools`) and **CPU package power** (RAPL). It stops when Vigil closes and is never started automatically.

## Install

```bash
git clone https://github.com/HamzaAkramv15/vigil.git
cd vigil
./install.sh
```

Per-user install into `~/.local` (`sudo ./install.sh` for system-wide `/usr/local`). Installs missing dependencies via `apt`, a launcher, the icon, a `.desktop` entry (with Unity launcher quicklist actions) and a **Ctrl+Shift+Esc** shortcut. Options: `--no-shortcut`, `--no-deps`.

Dependencies: `python3-gi gir1.2-gtk-3.0 python3-psutil python3-cairo`. Optional: `libnotify-bin`, `pciutils`, `gir1.2-ayatanaappindicator3-0.1`, `intel-gpu-tools`, `nvidia-utils` (NVIDIA stats), `network-manager` (SSID).

## Use

```bash
vigil                    # open
vigil --page processes   # overview, processes, resources, cpu, memory, gpu, sensors, battery,
                         # storage, network, services, diagnose, alerts, system
vigil --background       # start hidden (alerts + panel indicator only)
```

Running it again (or pressing the shortcut) focuses the existing window. From a checkout without installing: `./bin/vigil`.

## Uninstall

```bash
./uninstall.sh           # --purge also deletes ~/.config/vigil (settings); history is in ~/.local/state/vigil
```

## Known limits

- Per-process network counts TCP/UDP payload sizes seen on the wire; very high throughput (>~100 MB/s) can make the Python helper drop packets.
- Charge limit changes are written to sysfs and many laptops reset them on reboot.
- Per-process GPU % is NVIDIA only.
