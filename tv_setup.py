"""Easy setup for screens: find Fire TVs on this network, test them, and see which
screens (Fire TV app, TV browser, phone, tablet, laptop) currently have the display open.

Built for the simple case - everything on one home network - so a new install at
someone else's house needs no IP addresses typed in. (Two-network setups like a TV
behind a second router keep using tv_wake_helper_url; discovery only scans this
machine's own network.)
"""
import ipaddress
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import fire_tv

APP_PACKAGE = "com.smartazan.tvdisplay"
TEST_FILENAME = "__test__"          # tv_status reports this for a test chime, never a real file
TEST_PLAY_ID_BASE = 1_000_000_000   # test play_ids never collide with real ones
TEST_TTL_S = 45                     # a test is offered to pollers for this long
ONLINE_S = 60                       # a screen counts as "connected" if it polled this recently
FORGET_S = 24 * 60 * 60             # screens not seen for a day drop off the list

_lock = threading.Lock()
_clients = {}
_tests = {}
_test_seq = 0


# ----------------- which screens have the display open -----------------

def _kind(user_agent, explicit=None):
    if explicit:
        return explicit[:40]
    ua = (user_agent or "").lower()
    if ua.startswith("dalvik"):
        return "Fire TV app (background)"
    if "aft" in ua or "silk" in ua:
        return "Fire TV"
    if "iphone" in ua or "ipad" in ua or "android" in ua or "mobile" in ua:
        return "Phone / tablet"
    return "Browser"


def record_poll(ip, user_agent, client_id=None, kind=None, name=None):
    """Called on every /tv_status poll (every 5s from each screen)."""
    kind = _kind(user_agent, kind)
    key = (client_id or f"{ip}|{kind}")[:80]
    with _lock:
        entry = _clients.get(key, {})
        _clients[key] = {
            "key": key, "ip": ip, "kind": kind,
            "name": (name or entry.get("name") or "")[:60],
            "first_seen": entry.get("first_seen", time.time()),
            "last_seen": time.time(),
        }


def clients():
    now = time.time()
    with _lock:
        for key in [k for k, v in _clients.items() if now - v["last_seen"] > FORGET_S]:
            del _clients[key]
        rows = [{**v, "seconds_ago": int(now - v["last_seen"]), "online": now - v["last_seen"] <= ONLINE_S}
                for v in _clients.values()]
    return sorted(rows, key=lambda r: (not r["online"], r["ip"], r["kind"]))


# ----------------- test chime -----------------

def start_test(target="all"):
    """target: "all", a screen's IP, or a screen's client id."""
    global _test_seq
    with _lock:
        _test_seq += 1
        _tests[target] = {"test_id": _test_seq, "expires": time.time() + TEST_TTL_S}
        return _test_seq


def pending_test(ip, client_id=None):
    now = time.time()
    with _lock:
        offers = [_tests.get(t) for t in ("all", ip, client_id) if t]
    offers = [t for t in offers if t and t["expires"] > now]
    return max(offers, key=lambda t: t["test_id"]) if offers else None


# ----------------- finding Fire TVs -----------------

def _local_networks():
    """This machine's own /24 home network(s)."""
    nets = set()
    ip = fire_tv._lan_ip()
    if ip and not ip.startswith("127."):
        nets.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[1] not in ("lo",) and not parts[1].startswith(("docker", "br-", "veth", "tailscale")):
                addr = parts[3]
                if addr.startswith(("192.168.", "10.", "172.")):
                    nets.add(ipaddress.ip_network(addr.split("/")[0] + "/24", strict=False))
    except Exception:
        pass
    return sorted(nets, key=str)


def _port_open(ip, port=5555, timeout=0.4):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _describe(ip):
    """What's at ip:5555 - model, whether we're allowed in, and whether our app is on it."""
    target = f"{ip}:5555"
    info = {"ip": ip, "model": "", "authorized": False, "app_installed": False, "awake": None}
    try:
        subprocess.run(["adb", "connect", target], capture_output=True, text=True, timeout=8)
        devices = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=8).stdout
        state = next((l.split()[1] for l in devices.splitlines() if l.startswith(target + "\t")), "")
        if state == "unauthorized":
            info["note"] = "Press Allow on the TV (tick 'Always allow from this computer'), then search again."
            return info
        if state != "device":
            info["note"] = "Found, but adb couldn't connect."
            return info
        info["authorized"] = True
        r = fire_tv._adb(ip, "shell", "getprop", "ro.product.model")
        info["model"] = (r.stdout.strip() if r else "") or "Android device"
        r = fire_tv._adb(ip, "shell", "pm", "list", "packages", APP_PACKAGE)
        info["app_installed"] = bool(r and APP_PACKAGE in (r.stdout or ""))
        info["awake"] = fire_tv._adb_is_awake(ip)
        if not info["app_installed"]:
            info["note"] = "The Smart Azan TV app isn't installed on this TV yet."
    except FileNotFoundError:
        info["note"] = "adb isn't installed on this machine."
    except Exception as e:
        info["note"] = f"Couldn't check it: {e}"
    return info


def discover():
    """Every device on this machine's home network(s) with network debugging (adb, port
    5555) switched on - normally Fire TVs - with what we know about each."""
    nets = _local_networks()
    hosts = [str(h) for net in nets for h in net.hosts()]
    with ThreadPoolExecutor(max_workers=64) as pool:
        found = [ip for ip, ok in zip(hosts, pool.map(_port_open, hosts)) if ok]
    with ThreadPoolExecutor(max_workers=4) as pool:
        tvs = list(pool.map(_describe, found))
    return {"networks": [str(n) for n in nets], "tvs": tvs}


# ----------------- testing one Fire TV -----------------

def test_tv(ip, action):
    """wake: wake the TV. play: wake it, open the Smart Azan TV app, and play the test
    chime on it. sleep: put it back to sleep."""
    if action == "sleep":
        fire_tv._adb(ip, "shell", "input", "keyevent", fire_tv._KEYCODE_SLEEP)
        time.sleep(2)
        return {"ok": not fire_tv._adb_is_awake(ip), "message": "Asleep." if not fire_tv._adb_is_awake(ip) else "It didn't go to sleep."}
    if not fire_tv._adb_is_awake(ip):
        fire_tv._adb(ip, "shell", "input", "keyevent", fire_tv._KEYCODE_WAKEUP)
        time.sleep(3)
    awake = fire_tv._adb_is_awake(ip)
    if action == "wake":
        return {"ok": awake, "message": "The TV is awake." if awake else
                "No response - is ADB debugging on, and was 'Allow' pressed on the TV?"}
    if not awake:
        return {"ok": False, "message": "Couldn't wake the TV, so the test chime wasn't sent."}
    fire_tv._adb(ip, "shell", "am", "start", "-n", f"{APP_PACKAGE}/.MainActivity")
    start_test(ip)
    return {"ok": True, "message": "Opened the Smart Azan TV app - you should hear the test chime within 10 seconds. "
                                   "Press Home on the remote to leave it."}
