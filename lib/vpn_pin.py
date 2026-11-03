#!/usr/bin/env python3
"""Run independent OpenVPN tunnels and pin selected IPv4 host routes on macOS."""
import argparse
import contextlib
import fcntl
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


class Error(Exception):
    pass


def run(args, check=True):
    try:
        result = subprocess.run([str(arg) for arg in args], text=True, capture_output=True)
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
    profiles = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise Error("cannot read profiles file: " + str(error)) from error
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
            try:
                addresses = socket.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_STREAM)
            except OSError as error:
                raise Error("could not resolve " + target + ": " + str(error)) from error
            for entry in addresses:
                ips.add(str(ipaddress.IPv4Address(entry[4][0])))
        else:
            ips.add(str(address))
    if not ips:
        raise Error("no IPv4 addresses resolved for " + profile.name)
    return sorted(ips)


def private_directory(path):
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        raise Error("state directory must be root-owned, private, and not a symlink: " + str(path))


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
    output = run(["/sbin/route", "-n", "get", ip], check=False)
    fields = {}
    for line in output.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            fields[key] = value.strip()
    if fields.get("destination") == ip and "HOST" in fields.get("flags", ""):
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


def pin_routes(directory, profile, state):
    if not isinstance(state["utun"], str) or not re.fullmatch(r"utun[0-9]+", state["utun"]):
        raise Error(profile.name + " has unfinished startup state; run down before retrying")
    desired = resolve_ips(profile)
    previous = list(state["routes"])
    interface = state["utun"]
    for ip in desired:
        current = exact_route(ip)
        if current is not None and (ip not in previous or current != interface):
            raise Error("exact host route conflict for " + ip + " via " + current)
    added, removed = [], []
    original = {ip for ip in previous if exact_route(ip) == interface}
    # Record intent first so an interrupted command never loses the possible route set.
    save_state(directory, profile.name, dict(state, pending_routes=sorted(set(previous + desired))))
    try:
        for ip in desired:
            if exact_route(ip) is None:
                add_route(ip, interface)
                added.append(ip)
        for ip in previous:
            if ip not in desired and exact_route(ip) == interface:
                remove_route(ip, interface)
                removed.append(ip)
        state["routes"] = desired
        save_state(directory, profile.name, state)
    except (Error, OSError, KeyboardInterrupt) as error:
        failures = []
        for ip in added:
            try:
                remove_route(ip, interface)
            except (Error, OSError) as rollback:
                failures.append(str(rollback))
        for ip in removed:
            try:
                add_route(ip, interface)
            except (Error, OSError) as rollback:
                failures.append(str(rollback))
        state["routes"] = sorted(ip for ip in set(previous + desired) if exact_route(ip) == interface)
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
    if any(exact_route(ip) == state["utun"] for ip in state["routes"]):
        raise Error("stale process state still has routes; inspect before manual cleanup: " + str(directory))
    state_path(directory, name).unlink()


def wrapper(directory, profile):
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


def up(directory, profile):
    state = read_state(directory, profile.name)
    if state:
        if owns_process(directory, profile.name, state):
            if state["utun"] is None:
                raise Error(profile.name + " has unfinished startup state; run down before retrying")
            print(profile.name + " already up on " + state["utun"])
            pin_routes(directory, profile, state)
            return
        clean_stale(directory, profile.name, state)
    resolve_ips(profile)  # Fail before starting a tunnel when configuration/DNS is unusable.
    config = wrapper(directory, profile)
    log = directory / (profile.name + ".log")
    pidfile = directory / (profile.name + ".pid")
    for path in (log, pidfile):
        if path.exists() or path.is_symlink():
            private_file(path)
            path.unlink()
    binary = executable("openvpn")
    run([binary, "--cd", profile.ovpn.parent, "--config", config,
         "--log", log, "--writepid", pidfile, "--connect-timeout", "20", "--verb", "3", "--daemon"])
    state = None
    for _ in range(30):
        if state is None and pidfile.exists():
            private_file(pidfile)
            pid = int(pidfile.read_text().strip())
            pending = {"pid": pid, "binary": binary, "started": process_value(pid, "lstart"), "utun": None, "routes": []}
            if owns_process(directory, profile.name, pending):
                state = pending
                save_state(directory, profile.name, state)
        if log.exists():
            private_file(log)
        text = log.read_text(errors="replace") if log.exists() else ""
        if "Initialization Sequence Completed" in text:
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
             "utun": interfaces[-1], "routes": []}
    if not owns_process(directory, profile.name, state):
        raise Error("OpenVPN process identity did not match; refusing route changes")
    save_state(directory, profile.name, state)
    print(profile.name + " up on " + state["utun"])
    pin_routes(directory, profile, state)


def down(directory, name):
    state = read_state(directory, name)
    if not state:
        print(name + " is down")
        return
    if not owns_process(directory, name, state):
        clean_stale(directory, name, state)
        print(name + " stale state cleared; no process killed")
        return
    for ip in list(state["routes"]):
        remove_route(ip, state["utun"])
        state["routes"].remove(ip)
        save_state(directory, name, state)
    if not owns_process(directory, name, state):
        raise Error("process identity changed; refusing to send a signal")
    os.kill(state["pid"], signal.SIGTERM)
    for _ in range(50):
        if not owns_process(directory, name, state):
            state_path(directory, name).unlink()
            print(name + " stopped")
            return
        time.sleep(0.1)
    raise Error("OpenVPN has not stopped yet; state retained for " + name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(human().pw_dir) / ".config/vpn-pin/profiles.conf")
    parser.add_argument("command", choices=["up", "down", "refresh", "reconnect", "status", "profiles", "kube", "doctor"])
    parser.add_argument("profile", nargs="?")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise Error("vpn-pin currently supports macOS only")
    config = args.config.expanduser().absolute()
    profiles = load_profiles(config)
    if args.command == "profiles":
        for profile in profiles.values():
            print("%s | %s | %s | %s | %s" % (profile.name, profile.ovpn, ",".join(profile.targets), profile.context, profile.aws_profile))
        return
    if args.command == "doctor":
        print(run(["/usr/sbin/netstat", "-rn", "-f", "inet"]))
        print("Check that the default route uses your intended upstream connection.")
        print("A VPN kill switch or local-network blocking setting can block another tunnel.")
        return
    if args.args and args.command != "kube":
        raise Error("unexpected arguments")
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
        user = human()
        if os.geteuid() == 0 and user.pw_uid != 0:
            command = ["/usr/bin/sudo", "-H", "-u", user.pw_name, "--"] + command
        os.execv(command[0], command)
    if os.geteuid() != 0:
        forwarded = [args.command] + ([args.profile] if args.profile else []) + args.args
        os.execv("/usr/bin/sudo", ["sudo", sys.executable, str(Path(__file__).resolve()),
                                 "--config", str(config)] + forwarded)
    os.umask(0o077)
    with state_directory(human().pw_uid) as directory:
        selected = list(profiles) if args.profile == "all" or args.profile is None else [args.profile]
        for name in selected:
            profile = profiles[name]
            if args.command == "up":
                up(directory, profile)
            elif args.command == "down":
                down(directory, name)
            elif args.command == "reconnect":
                down(directory, name)
                up(directory, profile)
            else:
                state = read_state(directory, name)
                running = state is not None and owns_process(directory, name, state)
                if args.command == "refresh":
                    if not running:
                        raise Error(name + " is not running")
                    pin_routes(directory, profile, state)
                else:
                    print("%s\t%s\t%s\t%s" % (name, "up" if running and state["utun"] else "starting" if running else "stale" if state else "down",
                          state["utun"] or "-" if state else "-", " ".join(ip + ("" if exact_route(ip) == state["utun"] else "(missing/changed)") for ip in state["routes"]) if state else "-"))


if __name__ == "__main__":
    try:
        main()
    except (Error, OSError, ValueError) as error:
        print("vpn-pin: " + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
