#!/usr/bin/env python3
"""Run independent OpenVPN tunnels and pin selected IPv4 host routes on macOS."""
import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import vpn_pin_pf as api_pf

VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
AWAKE_ONLY = ("Awake-only support: tunnels and PF exceptions across sleep, including sleep/wake recovery, "
              "are unsupported. Stop clients and watchers, complete 'vpn-pin down PROFILE' and verify cleanup "
              "before sleep. After unexpected sleep or failed cleanup, inspect retained state; do not force reconnect.")


class Error(Exception):
    pass


class ContinuityError(Error):
    pass


class ResolutionError(Error):
    pass


def run(args, check=True, timeout=30):
    try:
        result = subprocess.run([str(arg) for arg in args], text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise Error("command timed out: " + str(args[0])) from error
    except OSError as error:
        raise Error("cannot run " + str(args[0]) + ": " + str(error)) from error
    if check and result.returncode:
        raise Error(result.stderr.strip() or "command failed: " + str(args[0]))
    return result.stdout.strip()


def executable(name):
    candidates = [shutil.which(name), "/opt/homebrew/bin/" + name,
                  "/usr/local/bin/" + name]
    if name == "openvpn":
        candidates += ["/opt/homebrew/opt/openvpn/sbin/openvpn",
                       "/usr/local/opt/openvpn/sbin/openvpn"]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return str(Path(path).resolve())
    raise Error("missing executable: " + name)


def human():
    uid = int(os.environ.get("SUDO_UID", os.getuid())) if os.geteuid() == 0 else os.getuid()
    return pwd.getpwuid(uid)


def as_user(args):
    user = human()
    if os.geteuid() == 0 and user.pw_uid != 0:
        args = ["/usr/bin/sudo", "-H", "-u", user.pw_name, "--"] + list(args)
    return run(args)


@dataclass
class Profile:
    name: str
    ovpn: Path
    targets: list
    context: str
    aws_profile: str


def load_profiles(path):
    try:
        return parse_profiles(path.read_text())
    except OSError as error:
        raise Error("cannot read profiles file: " + str(error)) from error


def parse_profiles(text):
    profiles = {}
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 5 or any(ord(char) < 32 for char in line):
            raise Error("line %d: expected five pipe-separated fields" % number)
        name, ovpn, targets, context, aws_profile = fields
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", name) or name == "all":
            raise Error("line %d: invalid profile name" % number)
        if name in profiles:
            raise Error("duplicate profile: " + name)
        if ovpn.startswith("~/"):
            ovpn = str(Path(human().pw_dir) / ovpn[2:])
        if not Path(ovpn).is_absolute() or any(char in ovpn for char in ('"', "\\")):
            raise Error("line %d: use an absolute .ovpn path without quotes or backslashes" % number)
        hosts = targets.split(",") if targets else []
        if any(not host or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host) for host in hosts):
            raise Error("line %d: invalid target; use comma-separated hostnames or IPv4 addresses" % number)
        if not hosts and not context:
            raise Error("line %d: configure a target or Kubernetes context" % number)
        profiles[name] = Profile(name, Path(ovpn), hosts, context, aws_profile)
    if not profiles:
        raise Error("profiles file contains no profiles")
    return profiles


def add_profile(path, name, ovpn, targets, context="", aws_profile=""):
    if os.geteuid() != human().pw_uid:
        raise Error("run profile add without sudo")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
        raise Error("configuration directory must be owned by you and not writable by others")
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise Error("unsafe configuration lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            before, text, mode = None, "", 0o600
        else:
            with os.fdopen(descriptor, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or before.st_mode & 0o022:
                    raise Error("unsafe profiles file; use an owned regular file without writable shared permissions")
                text, mode = source.read().decode("utf-8"), stat.S_IMODE(before.st_mode)
        ovpn = str(Path(ovpn).expanduser().absolute())
        fields = [name, ovpn, ",".join(targets), context, aws_profile]
        if any("|" in field or any(ord(char) < 32 for char in field) for field in fields):
            raise Error("profile fields cannot contain pipe characters or control characters")
        proposed = text + ("\n" if text and not text.endswith("\n") else "") + "|".join(fields) + "\n"
        parse_profiles(proposed)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".profiles-")
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(proposed)
                output.flush()
                os.fsync(output.fileno())
                if before is not None:
                    os.fchown(output.fileno(), -1, before.st_gid)
                os.fchmod(output.fileno(), mode)
            try:
                current = path.lstat()
            except FileNotFoundError:
                current = None
            fingerprint = lambda value: None if value is None else (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_mode, value.st_nlink)
            if fingerprint(current) != fingerprint(before):
                raise Error("profiles file changed concurrently; retry your command")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        os.close(lock)
    print("Added " + name + "; run vpn-pin validate " + name)


def lookup_ipv4(target):
    code = "import json,socket,sys; print(json.dumps(sorted({e[4][0] for e in socket.getaddrinfo(sys.argv[1],None,socket.AF_INET,socket.SOCK_STREAM)})))"
    try:
        return json.loads(run([sys.executable, "-I", "-c", code, target], timeout=10))
    except Error as error:
        raise ResolutionError("could not resolve " + target + "; check DNS/VPN settings") from error


def resolve_ips(profile):
    targets = list(profile.targets)
    if profile.context:
        endpoint = as_user([executable("kubectl"), "--context=" + profile.context,
                            "config", "view", "--minify", "-o",
                            "jsonpath={.clusters[0].cluster.server}"])
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise Error("invalid API endpoint for context " + profile.context)
        targets.append(parsed.hostname)
    ips = set()
    for target in targets:
        try:
            address = ipaddress.IPv4Address(target)
        except ipaddress.AddressValueError:
            if re.fullmatch(r"[0-9.]+", target):
                raise Error("invalid IPv4 address: " + target)
            for address in lookup_ipv4(target):
                ips.add(str(ipaddress.IPv4Address(address)))
        else:
            ips.add(str(address))
    if not ips:
        raise ResolutionError("no IPv4 addresses resolved for " + profile.name)
    return sorted(ips)


def private_directory(path, create=True):
    if create and not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        raise Error("state directory must be root-owned, private, and not a symlink: " + str(path))


def snapshot_directory(uid):
    directory = Path("/var/run/vpn-pin") / str(uid)
    for path in (directory.parent, directory):
        try:
            private_directory(path, create=False)
        except FileNotFoundError:
            return None
    return directory


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
        raise Error("unsafe state file: " + str(path))


@contextlib.contextmanager
def state_directory(uid):
    base = Path("/var/run/vpn-pin")
    private_directory(base)
    directory = base / str(uid)
    private_directory(directory)
    lock = base / "lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private_file(lock)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield directory
    finally:
        os.close(descriptor)


def state_path(directory, name):
    return directory / (name + ".json")


def read_state(directory, name):
    path = state_path(directory, name)
    if not path.exists() and not path.is_symlink():
        return None
    private_file(path)
    try:
        state = json.loads(path.read_text())
        if (not isinstance(state["pid"], int) or state["pid"] <= 1
                or not isinstance(state["binary"], str) or not Path(state["binary"]).is_absolute()
                or not (state["utun"] is None and state["routes"] == []
                        or isinstance(state["utun"], str) and re.fullmatch(r"utun[0-9]+", state["utun"]))
                or not isinstance(state["started"], str)):
            raise ValueError("invalid process identity")
        if "ifindex" in state and (type(state["ifindex"]) is not int or state["ifindex"] <= 0):
            raise ValueError("invalid interface identity")
        for ip in state["routes"]:
            if str(ipaddress.IPv4Address(ip)) != ip:
                raise ValueError("invalid route")
        if "pending_routes" in state:
            raise Error("unfinished route update; inspect recorded intent and routing table before manual recovery: " + str(path))
        return state
    except (KeyError, ValueError, TypeError) as error:
        raise Error("invalid state for " + name) from error


def save_state(directory, name, state):
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=name + ".")
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(state, output)
            output.write("\n")
        os.replace(temporary, state_path(directory, name))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_value(pid, field):
    return run(["/bin/ps", "-ww", "-p", str(pid), "-o", field + "="], check=False)


def owns_process(directory, name, state):
    pid = state["pid"]
    if not isinstance(pid, int) or pid <= 1:
        return False
    command = process_value(pid, "command")
    return (process_value(pid, "comm") == state["binary"]
            and process_value(pid, "lstart") == state["started"]
            and "--config " + str(directory / (name + ".conf")) + " " in command + " ")


def exact_route(ip):
    output = run(["/sbin/route", "-n", "get", ip])
    fields = {}
    for line in output.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            fields[key] = value.strip()
    if (not fields.get("destination") or not fields.get("interface")
            or not re.fullmatch(r"<[A-Z0-9_]+(?:,[A-Z0-9_]+)*>", fields.get("flags", ""))):
        raise Error("cannot determine route ownership for " + ip + "; malformed route lookup")
    flags = set(fields["flags"].strip("<>").split(","))
    # macOS caches fallback lookups as cloned host routes; these are not explicit pins.
    if fields.get("destination") == ip and "HOST" in flags and "WASCLONED" not in flags:
        return fields.get("interface")
    return None


def add_route(ip, interface):
    run(["/sbin/route", "-n", "add", "-host", ip, "-interface", interface])


def remove_route(ip, interface):
    current = exact_route(ip)
    if current is None:
        return
    if current != interface:
        raise Error("route ownership changed for " + ip + "; not deleting it")
    run(["/sbin/route", "-n", "delete", "-host", ip])


def route_plan(profile, state, desired=None):
    desired = resolve_ips(profile) if desired is None else desired
    previous = state["routes"] if state else []
    interface = state["utun"] if state else None
    current = {ip: exact_route(ip) for ip in set(previous + desired)}
    conflicts = {ip: current[ip] for ip in desired if current[ip] is not None
                 and (ip not in previous or current[ip] != interface)}
    return {"desired": desired, "add": [ip for ip in desired if current[ip] is None],
            "remove": [ip for ip in previous if ip not in desired and current[ip] == interface],
            "conflicts": conflicts}


def require_interface(state):
    if not state.get("ifindex"):
        raise Error("interface identity unavailable; stop with the previous version or inspect legacy state manually")
    try:
        current = socket.if_nametoindex(state["utun"])
    except OSError as error:
        if state["utun"] not in dict(socket.if_nameindex()).values():
            raise ContinuityError("tunnel interface disappeared; inspect before recovery") from error
        raise Error("cannot inspect tunnel interface; inspect before recovery") from error
    if current != state["ifindex"]:
        raise ContinuityError("tunnel interface identity changed; inspect before recovery")


def log_snapshot(path):
    private_file(path)
    with path.open("rb") as source:
        info = os.fstat(source.fileno())
        data = source.read(4 * 1024 * 1024 + 1)
    # shortcut: stop at 4 MiB; add bounded streaming continuity checks if long sessions need larger logs.
    if len(data) > 4 * 1024 * 1024:
        raise Error("tunnel log exceeds continuity-check limit; inspect and restart explicitly")
    return data, {"device": info.st_dev, "inode": info.st_ino, "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}


def checked_log_tail(directory, name, state):
    guard = state.get("log_guard")
    if not isinstance(guard, dict):
        raise Error("log continuity unavailable; stop with the previous version or inspect legacy state manually")
    data, current = log_snapshot(directory / (name + ".log"))
    try:
        size = guard["size"]
        if (type(size) is not int or size < 0 or size > len(data)
                or any(current[key] != guard[key] for key in ("device", "inode"))
                or hashlib.sha256(data[:size]).hexdigest() != guard["sha256"]):
            raise Error("tunnel log was replaced or reset; inspect before recovery")
    except (KeyError, TypeError) as error:
        raise Error("invalid tunnel log continuity record") from error
    tail = data[size:]
    if re.search(rb"SIGTERM|SIGINT", tail):
        raise Error("tunnel termination requested; inspect before recovery")
    return tail


def require_lifetime(directory, name, state):
    tail = checked_log_tail(directory, name, state)
    require_interface(state)
    if re.search(rb"Opened utun device|Closing TUN/TAP interface|SIGHUP", tail):
        raise ContinuityError("tunnel lifecycle changed; inspect before recovery")


def pin_routes(directory, profile, state, desired=None):
    api_pf.guard(directory, profile.name)
    if not isinstance(state["utun"], str) or not re.fullmatch(r"utun[0-9]+", state["utun"]):
        raise Error(profile.name + " has unfinished startup state; run down before retrying")
    require_lifetime(directory, profile.name, state)
    plan = route_plan(profile, state, desired)
    desired = plan["desired"]
    previous = list(state["routes"])
    interface = state["utun"]
    if plan["conflicts"]:
        raise Error("exact host route conflict: " + ", ".join(ip + " via " + interface for ip, interface in plan["conflicts"].items()))
    added, removed, inflight = [], [], None
    original = {ip for ip in previous if exact_route(ip) == interface}
    # Record intent first so an interrupted command never loses the possible route set.
    save_state(directory, profile.name, dict(state, pending_routes=sorted(set(previous + desired))))
    try:
        for ip in plan["add"]:
            require_lifetime(directory, profile.name, state)
            if exact_route(ip) is not None:
                raise Error("route changed after planning: " + ip)
            inflight = ip
            add_route(ip, interface)
            added.append(ip)
            inflight = None
        for ip in plan["remove"]:
            require_lifetime(directory, profile.name, state)
            remove_route(ip, interface)
            removed.append(ip)
        if any(exact_route(ip) != interface for ip in desired):
            raise Error("route ownership changed during refresh")
        state["routes"] = desired
        save_state(directory, profile.name, state)
    except (Error, OSError, KeyboardInterrupt) as error:
        failures = []
        for ip in added:
            try:
                require_lifetime(directory, profile.name, state)
                remove_route(ip, interface)
            except (Error, OSError) as rollback:
                failures.append(str(rollback))
        for ip in removed:
            try:
                require_lifetime(directory, profile.name, state)
                add_route(ip, interface)
            except (Error, OSError) as rollback:
                failures.append(str(rollback))
        if inflight is not None and exact_route(inflight) is not None:
            raise Error("route command outcome is uncertain; inspect unfinished journal before manual recovery: " + str(state_path(directory, profile.name))) from error
        state["routes"] = sorted(ip for ip in set(previous + added) if exact_route(ip) == interface)
        if set(state["routes"]) != original:
            failures.append("changed routes retained for cleanup")
        try:
            save_state(directory, profile.name, state)
        except OSError:
            failures.append("could not save recovery state; inspect recorded intent in " + str(state_path(directory, profile.name)))
        raise Error(str(error) + ("; rollback incomplete: " + "; ".join(failures) if failures else "; previous routes restored")) from error
    for ip in desired:
        print("  " + ip + " -> " + interface)


def clean_stale(directory, name, state):
    api_pf.guard(directory, name)
    if any(exact_route(ip) == state["utun"] for ip in state["routes"]):
        raise Error("stale process state still has routes; inspect before manual cleanup: " + str(directory))
    state_path(directory, name).unlink()


def wrapper(directory, profile):
    api_pf.guard(directory, profile.name)
    if not profile.ovpn.is_file():
        raise Error("OpenVPN configuration not found: " + str(profile.ovpn))
    path = directory / (profile.name + ".conf")
    if path.exists() or path.is_symlink():
        private_file(path)
    path.write_text('config "' + str(profile.ovpn) + '"\n'
                    'pull-filter ignore "redirect-gateway"\n'
                    'pull-filter ignore "route 0.0.0.0 0.0.0.0"\n'
                    'route-nopull\nroute-noexec\npersist-tun\nping 10\nping-restart 60\n')
    path.chmod(0o600)
    return path


def up(directory, profile, desired=None):
    api_pf.guard(directory, profile.name)
    print("Warning: " + AWAKE_ONLY, file=sys.stderr, flush=True)
    state = read_state(directory, profile.name)
    if state and desired is not None:
        raise Error("runtime state appeared before fresh startup; inspect before recovery")
    if state:
        if owns_process(directory, profile.name, state):
            if state["utun"] is None:
                raise Error(profile.name + " has unfinished startup state; run down before retrying")
            print(profile.name + " already up on " + state["utun"])
            pin_routes(directory, profile, state, desired)
            return state
        clean_stale(directory, profile.name, state)
    desired = resolve_ips(profile) if desired is None else desired
    config = wrapper(directory, profile)
    log = directory / (profile.name + ".log")
    pidfile = directory / (profile.name + ".pid")
    for path in (log, pidfile):
        if path.exists() or path.is_symlink():
            private_file(path)
            path.unlink()
    binary = executable("openvpn")
    run([binary, "--cd", profile.ovpn.parent, "--config", config,
         "--log-append", log, "--writepid", pidfile, "--connect-timeout", "20", "--verb", "3", "--daemon"])
    state = None
    for _ in range(30):
        if state is None and pidfile.exists():
            private_file(pidfile)
            pid = int(pidfile.read_text().strip())
            pending = {"pid": pid, "binary": binary, "started": process_value(pid, "lstart"), "utun": None, "routes": []}
            if owns_process(directory, profile.name, pending):
                state = pending
                save_state(directory, profile.name, state)
        data, guard = log_snapshot(log) if log.exists() else (b"", None)
        text = data.decode(errors="replace")
        lifecycle = max(text.rfind(event) for event in ("Opened utun device", "Closing TUN/TAP interface", "SIGHUP"))
        if text.rfind("Initialization Sequence Completed") > lifecycle:
            break
        time.sleep(1)
    else:
        if state is not None and owns_process(directory, profile.name, state):
            os.kill(state["pid"], signal.SIGTERM)
        raise Error("tunnel did not initialize; inspect " + str(log) + "; no API routes changed")
    interfaces = re.findall(r"Opened utun device (utun[0-9]+)", text)
    if not interfaces or not pidfile.exists():
        raise Error("cannot identify initialized OpenVPN tunnel; inspect " + str(log))
    private_file(pidfile)
    pid = int(pidfile.read_text().strip())
    state = {"pid": pid, "binary": binary, "started": process_value(pid, "lstart"),
             "utun": interfaces[-1], "ifindex": socket.if_nametoindex(interfaces[-1]), "log_guard": guard, "routes": []}
    if not owns_process(directory, profile.name, state):
        raise Error("OpenVPN process identity did not match; refusing route changes")
    save_state(directory, profile.name, state)
    print(profile.name + " up on " + state["utun"])
    pin_routes(directory, profile, state, desired)
    return state


def down(directory, name, expected=None):
    permission = api_pf.load(directory, name)
    api_pf.require_reference(directory, name, permission)
    if permission:
        api_pf.revoke(directory, name, permission,
                      lambda: require_pf_session(directory, name, permission["session"], permission["source"], permission["token"]))
    state = read_state(directory, name)
    if expected is not None and state != expected:
        raise Error("runtime state changed before automatic stop; inspect before recovery")
    if not state:
        print(name + " is down")
        return
    if not owns_process(directory, name, state):
        if expected is not None:
            raise Error("process ownership lost before automatic stop; inspect before recovery")
        clean_stale(directory, name, state)
        print(name + " stale state cleared; no process killed")
        return
    if expected is not None:
        checked_log_tail(directory, name, state)
    for ip in list(state["routes"]):
        if exact_route(ip) is not None:
            require_lifetime(directory, name, state)
            remove_route(ip, state["utun"])
        state["routes"].remove(ip)
        save_state(directory, name, state)
    if expected is not None:
        if read_state(directory, name) != state:
            raise Error("runtime state changed during automatic stop; inspect before recovery")
        checked_log_tail(directory, name, state)
    if not owns_process(directory, name, state):
        raise Error("process identity changed; refusing to send a signal")
    os.kill(state["pid"], signal.SIGTERM)
    for _ in range(50):
        if expected is not None:
            try:
                os.kill(state["pid"], 0)
            except ProcessLookupError:
                stopped = True
            except OSError as error:
                raise Error("cannot confirm process exit; state retained for " + name) from error
            else:
                stopped = False
        else:
            stopped = not owns_process(directory, name, state)
        if stopped:
            state_path(directory, name).unlink()
            print(name + " stopped")
            return
        time.sleep(0.1)
    raise Error("OpenVPN has not stopped yet; state retained for " + name)


def validate_profiles(profiles, resolve=False):
    failures = []
    for profile in profiles:
        if not profile.ovpn.is_file():
            failures.append(profile.name + ": OpenVPN file missing (contents not inspected)")
        if resolve:
            try:
                print(profile.name + ": IPv4 targets " + " ".join(resolve_ips(profile)))
            except Error as error:
                failures.append(profile.name + ": " + str(error))
        if not failures or not failures[-1].startswith(profile.name + ":"):
            print(profile.name + ": profile metadata valid; OpenVPN authentication not tested")
    if failures:
        raise Error("; ".join(failures))


def show_plan(directory, profile):
    state = read_state(directory, profile.name) if directory else None
    running = state is not None and owns_process(directory, profile.name, state)
    if state and not running:
        raise Error(profile.name + ": stale process state; inspect before planning changes")
    if running and state["utun"] is not None:
        require_lifetime(directory, profile.name, state)
    plan = route_plan(profile, state)
    print(profile.name + ": snapshot only; " + ("tunnel " + str(state["utun"]) if running else "start the tunnel before applying routes"))
    for ip in plan["desired"]:
        if ip in plan["conflicts"]:
            print("  CONFLICT " + ip + " via " + plan["conflicts"][ip])
        else:
            print("  " + ("ADD " if ip in plan["add"] else "KEEP ") + ip)
    for ip in plan["remove"]:
        print("  REMOVE " + ip)
    if plan["conflicts"]:
        raise Error("plan has conflicts; no changes made")


def doctor(config, name=None, port=None):
    problems = []
    for tool in ("openvpn",):
        try:
            print("OK " + tool + ": " + executable(tool))
        except Error as error:
            problems.append(str(error))
    try:
        profiles = load_profiles(config)
    except Error as error:
        raise Error(str(error) + "; use vpn-pin profile add to create a profile") from error
    if name and name not in profiles:
        raise Error("unknown profile: " + name)
    directory = snapshot_directory(human().pw_uid) if os.geteuid() == 0 else None
    print("Default route:\n" + run(["/sbin/route", "-n", "get", "default"], check=False))
    for profile in ([profiles[name]] if name else profiles.values()):
        print("Profile " + profile.name)
        for tool in (["kubectl"] if profile.context else []) + (["aws-vault"] if profile.aws_profile else []):
            try:
                print("  OK " + tool + ": " + executable(tool))
            except Error as error:
                problems.append(profile.name + ": " + str(error))
        if not profile.ovpn.is_file():
            problems.append(profile.name + ": OpenVPN configuration file missing")
        state = read_state(directory, profile.name) if directory else None
        running = state is not None and owns_process(directory, profile.name, state)
        if running and state["utun"] is not None:
            try:
                require_lifetime(directory, profile.name, state)
            except Error as error:
                problems.append(profile.name + ": " + str(error))
                running = False
        print("  tunnel: " + ("up on " + str(state["utun"]) if running else "stale" if state else "not running" if os.geteuid() == 0 else "run with sudo for process/route ownership checks"))
        try:
            ips = resolve_ips(profile)
        except Error as error:
            problems.append(profile.name + ": " + str(error))
            continue
        for ip in ips:
            interface = exact_route(ip)
            owned = running and state["utun"] is not None and ip in state["routes"] and interface == state["utun"]
            print("  " + ip + ": " + ("owned route via " + interface if owned else "exact route via " + interface if interface else "using fallback routing"))
            if state and not owned:
                problems.append(profile.name + ": missing/changed owned route for " + ip)
            if port:
                if not owned:
                    problems.append(ip + ": TCP probe skipped until the tunnel and route are verified")
                    continue
                try:
                    with socket.create_connection((ip, port), timeout=2):
                        print("  TCP " + ip + ":" + str(port) + " reachable (authentication not tested)")
                except OSError:
                    problems.append(ip + ": TCP connection failed; check service, firewall, and upstream VPN policy")
    print("VPN kill-switch/local-network policies can block another tunnel; they are not changed or conclusively diagnosed here.")
    if problems:
        raise Error("; ".join(problems))


def client_identity(profile):
    try:
        info = profile.ovpn.stat()
    except OSError as error:
        raise Error("cannot inspect client configuration metadata") from error
    if not stat.S_ISREG(info.st_mode):
        raise Error("client configuration is not a regular file")
    return tuple(getattr(info, field) for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_gid"))


def restart_owned(directory, config, profile, state, desired, client_stamp):
    api_pf.guard(directory, profile.name)
    if read_state(directory, profile.name) != state or not owns_process(directory, profile.name, state):
        raise Error("runtime process or state changed; refusing automatic reconnect")
    try:
        require_lifetime(directory, profile.name, state)
    except ContinuityError:
        pass
    else:
        raise Error("automatic reconnect requires verified tunnel continuity loss")

    def unchanged():
        if load_profiles(config).get(profile.name) != profile or client_identity(profile) != client_stamp:
            raise Error("profile or client configuration changed; refusing automatic reconnect")

    ips = sorted(set(state["routes"] + desired))

    def absent():
        if any(exact_route(ip) is not None for ip in ips):
            raise Error("recorded or target host routes remain; inspect before automatic reconnect")

    unchanged()
    absent()
    print("Restarting " + profile.name + " once with a fresh verified tunnel session", flush=True)
    # Keep the bounded stop/start transition exclusive; never adopt a recreated interface.
    down(directory, profile.name, expected=state)
    unchanged()
    absent()
    created = up(directory, profile, desired)
    current = read_state(directory, profile.name)
    if not current or current != created or not owns_process(directory, profile.name, current):
        raise Error("new tunnel process is not owned; inspect before recovery")
    require_lifetime(directory, profile.name, current)
    if set(current["routes"]) != set(desired) or any(exact_route(ip) != current["utun"] for ip in desired):
        raise Error("new tunnel routes are not verified; inspect before recovery")
    unchanged()
    return current


def watch(config, name, interval, auto_reconnect=False):
    delay = interval
    attempted = False
    frozen = load_profiles(config).get(name) if auto_reconnect else None
    if auto_reconnect and frozen is None:
        raise Error("watched profile was removed; stopping")
    client_stamp = client_identity(frozen) if auto_reconnect else None
    identity = lambda state: tuple(state.get(key) for key in ("pid", "started", "binary", "utun", "ifindex", "log_guard"))
    with state_directory(human().pw_uid) as directory:
        api_pf.guard(directory, name)
        initial = read_state(directory, name)
        if not initial or not owns_process(directory, name, initial) or initial["utun"] is None:
            raise Error("watch requires an initialized, owned tunnel; run up explicitly after inspection")
        require_lifetime(directory, name, initial)
        expected = identity(initial)
    print("Warning: " + AWAKE_ONLY, file=sys.stderr, flush=True)
    print("Watching " + name + "; Ctrl-C stops watching and leaves the tunnel running", flush=True)
    while True:
        profiles = load_profiles(config)
        if name not in profiles:
            raise Error("watched profile was removed; stopping")
        profile = profiles[name]
        try:
            desired = resolve_ips(profile)
        except ResolutionError as error:
            print(str(error) + "; routes preserved; retrying in " + str(delay) + "s", file=sys.stderr, flush=True)
            time.sleep(delay)
            delay = min(max(300, interval), delay * 2)
            continue
        with state_directory(human().pw_uid) as directory:
            api_pf.guard(directory, name)
            state = read_state(directory, name)
            if not state or identity(state) != expected:
                raise Error("watched tunnel changed; restart watch explicitly after inspection")
            if not owns_process(directory, name, state):
                raise Error("watch requires an initialized, owned tunnel; run up explicitly after inspection")
            try:
                require_lifetime(directory, name, state)
            except ContinuityError:
                if not auto_reconnect:
                    raise
                if attempted:
                    raise Error("automatic reconnect attempt exhausted; inspect and restart watch explicitly")
                if profile != frozen:
                    raise Error("watched profile changed; refusing automatic reconnect")
                attempted = True
                try:
                    state = restart_owned(directory, config, frozen, state, desired, client_stamp)
                except (Error, OSError) as error:
                    raise Error("automatic reconnect failed; inspect " + str(state_path(directory, name)) + ": " + str(error)) from error
                expected = identity(state)
            else:
                plan = route_plan(profile, state, desired)
                if plan["add"] or plan["remove"] or plan["conflicts"]:
                    if not owns_process(directory, name, state):
                        raise Error("process identity changed; stopping watch")
                    pin_routes(directory, profile, state, desired)
        delay = interval
        time.sleep(interval)


def require_pf_session(directory, name, expected, source=None, token=None):
    state = read_state(directory, name)
    if (not state or (token is not None and state.get("pf_token") != token)
            or any(state.get(key) != value for key, value in expected.items())
            or not owns_process(directory, name, state)):
        raise Error("PF tunnel process/state changed; refusing interface adoption")
    require_lifetime(directory, name, state)
    if not state["routes"] or any(exact_route(ip) != state["utun"] for ip in state["routes"]):
        raise Error("PF target route ownership changed; inspect before state cleanup")
    if source is not None and api_pf.source_address(state) != source:
        raise Error("PF source address changed; inspect before state cleanup")
    return state


def supervise_api_pf(config, name):
    def interrupt(*args):
        raise KeyboardInterrupt()
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    handlers = {sig: signal.signal(sig, interrupt) for sig in signals}
    mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, set(signals))
    permission = None
    try:
        profile = load_profiles(config)[name]
        stamp = client_identity(profile)
        desired = resolve_ips(profile)
        print("Warning: " + AWAKE_ONLY, file=sys.stderr, flush=True)
        with state_directory(human().pw_uid) as directory:
            state = read_state(directory, name)
            if not state or state["utun"] is None:
                raise Error("api-pf requires an initialized owned tunnel; run up first")
            require_pf_session(directory, name, state)
            if set(state["routes"]) != set(desired):
                raise Error("PF targets differ from current resolution; run down/up before opting in")
            def verify_apply():
                require_pf_session(directory, name, state)
                if load_profiles(config).get(name) != profile or client_identity(profile) != stamp:
                    raise Error("PF profile/client configuration changed before apply")
            def reserve(record):
                state["pf_token"] = record["token"]
                save_state(directory, name, state)
            grant_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(signals))
            try:
                permission = api_pf.enable(directory, name, state, verify_apply, reserve)
            finally:
                # Deliver a pending stop only after the caller owns the returned permission.
                signal.pthread_sigmask(signal.SIG_SETMASK, grant_mask)
        print("Supervising API TCP 443 PF exceptions for " + name + "; Ctrl-C revokes them and leaves the tunnel running", flush=True)
        while True:
            with state_directory(human().pw_uid) as directory:
                live = api_pf.current(directory, name, permission)
                if live["phase"] == "closed":
                    if api_pf.anchor_rules(live):
                        raise Error("closed PF anchor is no longer empty; inspect before recovery")
                    return
                if load_profiles(config).get(name) != profile or client_identity(profile) != stamp:
                    raise Error("PF profile/client configuration changed; revoking permissions")
                require_pf_session(directory, name, permission["session"], permission["source"], permission["token"])
                if api_pf.anchor_rules(live) != live["expected"]:
                    raise Error("PF permissions changed; inspect before recovery")
            time.sleep(1)
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, set(signals))
        try:
            if permission is not None:
                with state_directory(human().pw_uid) as directory:
                    live = api_pf.load(directory, name)
                    if live is None and api_pf.runtime_token(directory, name) == permission["token"]:
                        api_pf.restore_for_revocation(directory, name, permission)
                        live = api_pf.load(directory, name)
                    if live and live["token"] == permission["token"]:
                        api_pf.revoke(directory, name, permission,
                                      lambda: require_pf_session(directory, name, permission["session"], permission["source"], permission["token"]))
                    elif api_pf.anchor_rules(permission):
                        raise Error("PF journal replaced while original anchor remains; manual inspection required")
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vpn-pin", description=__doc__, epilog=AWAKE_ONLY)
    parser.add_argument("--version", action="version", version="vpn-pin " + VERSION)
    parser.add_argument("--config", type=Path, default=Path(human().pw_dir) / ".config/vpn-pin/profiles.conf")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("up", "down", "refresh", "reconnect", "status", "plan", "validate", "watch", "doctor", "kube", "api-pf"):
        subparser = commands.add_parser(command, epilog=AWAKE_ONLY if command in ("up", "reconnect", "watch", "api-pf") else None)
        subparser.add_argument("profile", nargs="?" if command in ("status", "doctor", "validate") else None)
        if command == "kube":
            subparser.add_argument("args", nargs=argparse.REMAINDER)
        if command == "watch":
            subparser.add_argument("--interval", type=float, default=30)
            subparser.add_argument("--auto-reconnect", action="store_true", help="allow one checked restart after awake-session continuity loss; sleep recovery unsupported")
        if command == "doctor":
            subparser.add_argument("--port", type=int)
        if command == "validate":
            subparser.add_argument("--resolve", action="store_true")
    commands.add_parser("profiles")
    profile_parser = commands.add_parser("profile").add_subparsers(dest="profile_command", required=True)
    add = profile_parser.add_parser("add")
    add.add_argument("name")
    add.add_argument("--ovpn", required=True)
    add.add_argument("--target", action="append", default=[])
    add.add_argument("--context", default="")
    add.add_argument("--aws-profile", default="")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise Error("vpn-pin currently supports macOS only")
    config = args.config.expanduser().absolute()
    if args.command == "profile":
        add_profile(config, args.name, args.ovpn, args.target, args.context, args.aws_profile)
        return
    if args.command == "doctor":
        if args.port is not None and (not 1 <= args.port <= 65535 or not args.profile):
            raise Error("--port requires a profile and a port from 1 to 65535")
        doctor(config, args.profile, args.port)
        return
    profiles = load_profiles(config)
    if args.command == "profiles":
        for profile in profiles.values():
            print("%s | %s | %s | %s | %s" % (profile.name, profile.ovpn, ",".join(profile.targets), profile.context, profile.aws_profile))
        return
    if args.command == "validate":
        if args.profile and args.profile not in profiles:
            raise Error("unknown profile: " + args.profile)
        validate_profiles([profiles[args.profile]] if args.profile else profiles.values(), args.resolve)
        return
    if args.command == "watch" and not 5 <= args.interval <= 3600:
        raise Error("watch interval must be between 5 and 3600 seconds")
    if args.profile not in profiles and not (args.profile == "all" and args.command in ("down", "reconnect")) and not (args.profile is None and args.command == "status"):
        raise Error("choose a configured profile (vpn-pin profiles)")
    if args.command == "kube":
        profile = profiles[args.profile]
        if not profile.context:
            raise Error("profile has no Kubernetes context")
        command = [executable("kubectl"), "--context=" + profile.context]
        command += args.args[1:] if args.args[:1] == ["--"] else args.args
        if profile.aws_profile:
            command = [executable("aws-vault"), "exec", profile.aws_profile, "--"] + command
        # kubectl credential plugins also need Homebrew tools when sudo restricts PATH.
        command = ["/usr/bin/env", "PATH=" + os.environ.get("PATH", os.defpath) + ":/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin"] + command
        user = human()
        if os.geteuid() == 0 and user.pw_uid != 0:
            command = ["/usr/bin/sudo", "-H", "-u", user.pw_name, "--"] + command
        os.execv(command[0], command)
    if os.geteuid() != 0:
        forwarded = list(argv if argv is not None else sys.argv[1:])
        while forwarded and (forwarded[0] == "--config" or forwarded[0].startswith("--config=")):
            del forwarded[:2 if forwarded[0] == "--config" else 1]
        os.execv("/usr/bin/sudo", ["sudo", sys.executable, str(Path(__file__).resolve()),
                                 "--config", str(config)] + forwarded)
    os.umask(0o077)
    if args.command == "plan":
        show_plan(snapshot_directory(human().pw_uid), profiles[args.profile])
        return
    if args.command == "watch":
        watch(config, args.profile, args.interval, args.auto_reconnect)
        return
    if args.command == "api-pf":
        supervise_api_pf(config, args.profile)
        return
    with state_directory(human().pw_uid) as directory:
        selected = list(profiles) if args.profile == "all" or args.profile is None else [args.profile]
        for name in selected:
            profile = profiles[name]
            if args.command == "up":
                up(directory, profile)
            elif args.command == "down":
                down(directory, name)
            elif args.command == "reconnect":
                api_pf.guard(directory, name)
                down(directory, name)
                up(directory, profile)
            else:
                state = read_state(directory, name)
                permission = api_pf.load(directory, name)
                running = state is not None and owns_process(directory, name, state)
                if args.command == "refresh":
                    if not running:
                        raise Error(name + " is not running")
                    pin_routes(directory, profile, state)
                else:
                    print("%s\t%s\t%s\t%s\tPF:%s" % (name, "up" if running and state["utun"] else "starting" if running else "stale" if state else "down",
                          state["utun"] or "-" if state else "-", " ".join(ip + ("" if exact_route(ip) == state["utun"] else "(missing/changed)") for ip in state["routes"]) if state else "-", permission["phase"] if permission else "off"))


if __name__ == "__main__":
    try:
        main()
    except (Error, OSError, ValueError) as error:
        print("vpn-pin: " + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
