"""Explicit, periodically supervised API-only PF permissions for macOS."""
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid


class Error(ValueError):
    pass


def command(args, text=None):
    try:
        result = subprocess.run([str(x) for x in args], input=text, text=True,
                                capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Error("PF command could not complete; retain the PF journal for inspection") from error
    if result.returncode:
        raise Error("PF command failed; retain the PF journal for inspection: " + str(args[0]))
    return result.stdout


def pf(*args, text=None):
    return command(["/sbin/pfctl", *args], text)


def normalize(text):
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


def path(directory, name):
    return directory / (name + ".pf.json")


def private_file(file):
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
        raise Error("unsafe PF journal: " + str(file))


def ipv4(value):
    if not isinstance(value, str) or str(ipaddress.IPv4Address(value)) != value:
        raise ValueError("invalid IPv4 address")
    return value


def render(record):
    session = record["session"]
    return "\n".join(
        'pass out quick on %s inet proto tcp from %s to %s port = 443 flags S/SA keep state (if-bound) label "vp-%s-%d"'
        % (session["utun"], record["source"], ip, record["token"][:16], index)
        for index, ip in enumerate(session["routes"]))


def validate(directory, record):
    try:
        session = record["session"]
        if (type(record["version"]) is not int or record["version"] != 1 or not re.fullmatch(r"[0-9a-f]{32}", record["token"])
                or not directory.name.isdigit()
                or record["anchor"] != "main/vpn-pin-" + directory.name + "-" + record["token"][:16]
                or record["phase"] not in ("prepared", "active", "rules-removed", "closed")
                or type(record["states_verified"]) is not bool
                or (record["phase"] == "closed") != record["states_verified"]
                or not re.fullmatch(r"[0-9a-f]{64}", record["root_hash"])
                or type(session["pid"]) is not int or session["pid"] <= 1
                or not isinstance(session["binary"], str) or not Path(session["binary"]).is_absolute()
                or not isinstance(session["started"], str) or not session["started"]
                or not re.fullmatch(r"utun[0-9]+", session["utun"])
                or type(session["ifindex"]) is not int or session["ifindex"] <= 0
                or not isinstance(session["log_guard"], dict)
                or not isinstance(session["routes"], list) or not session["routes"]
                or len(set(session["routes"])) != len(session["routes"])):
            raise ValueError("invalid ownership record")
        ipv4(record["source"])
        for ip in session["routes"]:
            ipv4(ip)
        if record["expected"] != render(record):
            raise ValueError("unexpected permission scope")
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise Error("invalid PF journal; inspect before lifecycle changes") from error
    return record


def load(directory, name):
    file = path(directory, name)
    try:
        private_file(file)
    except FileNotFoundError:
        return None
    try:
        return validate(directory, json.loads(file.read_text()))
    except (OSError, ValueError, TypeError) as error:
        raise Error("cannot read PF journal; inspect " + str(file)) from error


def store(directory, name, record):
    validate(directory, record)
    file = path(directory, name)
    if file.exists() or file.is_symlink():
        private_file(file)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=name + ".pf.")
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(record, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def current(directory, name, expected):
    record = load(directory, name)
    if not record or any(record[key] != expected[key] for key in
                         ("version", "token", "anchor", "session", "source", "expected", "root_hash")):
        raise Error("PF supervisor/journal identity changed; refusing mutation")
    return record


def anchor_rules(record):
    return normalize(pf("-a", record["anchor"], "-s", "rules"))


def runtime_token(directory, name):
    file = directory / (name + ".json")
    try:
        private_file(file)
    except FileNotFoundError:
        return None
    try:
        state = json.loads(file.read_text())
        if not isinstance(state, dict):
            raise ValueError("invalid runtime state")
        if "pf_token" not in state:
            return None
        token = state["pf_token"]
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("invalid PF reference")
        return token
    except (OSError, ValueError, TypeError) as error:
        raise Error("runtime PF reference cannot be read; inspect before lifecycle changes") from error


def require_reference(directory, name, record):
    token = runtime_token(directory, name)
    if token is not None and (record is None or record["token"] != token):
        raise Error("PF journal missing or replaced for this runtime session; lifecycle changes refused")


def restore_for_revocation(directory, name, expected):
    if load(directory, name) is not None or runtime_token(directory, name) != expected["token"]:
        raise Error("cannot recover the original PF journal; inspect before recovery")
    actual = anchor_rules(expected)
    if actual and actual != expected["expected"]:
        raise Error("original PF anchor changed; refusing journal recovery")
    record = copy.deepcopy(expected)
    record["phase"] = "prepared"
    record["states_verified"] = False
    store(directory, name, record)


def guard(directory, name):
    record = load(directory, name)
    require_reference(directory, name, record)
    if record:
        if record["phase"] != "closed":
            raise Error("PF-managed profile is frozen; run down before lifecycle or route changes")
        if anchor_rules(record):
            raise Error("closed PF anchor is no longer empty; inspect before lifecycle changes")
    return record


def source_address(session):
    output = command(["/sbin/ifconfig", session["utun"]])
    addresses = re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)", output)
    if len(addresses) != 1:
        raise Error("PF requires one unambiguous tunnel IPv4 source")
    return ipv4(addresses[0])


def same_session(record, state):
    return state is not None and all(state.get(key) == value for key, value in record["session"].items())


def enable(directory, name, state, verify, reserve):
    guard(directory, name)
    verify()
    root = pf("-s", "rules")
    if not re.search(r'^anchor "main/\*" all$', root, re.MULTILINE):
        raise Error('PF API mode requires an existing main/* hook; root rules are never reloaded')
    if not re.search(r"^Status: Enabled\b", pf("-s", "info")):
        raise Error("PF must already be enabled; vpn-pin will not enable or disable it")
    token = uuid.uuid4().hex
    record = {"version": 1, "token": token, "anchor": "main/vpn-pin-" + directory.name + "-" + token[:16],
              "session": copy.deepcopy({key: state[key] for key in
                                        ("pid", "started", "binary", "utun", "ifindex", "log_guard", "routes")}),
              "source": source_address(state), "phase": "prepared", "states_verified": False,
              "root_hash": fingerprint(root)}
    record["expected"] = render(record)
    children = {line.strip().split("/")[-1] for line in pf("-a", "main", "-s", "Anchors").splitlines()}
    if record["anchor"].split("/")[-1] in children or anchor_rules(record):
        raise Error("PF anchor already exists; refusing takeover")
    if pf("-i", state["utun"], "-s", "states").strip():
        raise Error("test/profile interface already has PF states; refusing address-pair cleanup ownership")
    normalized = normalize(pf("-n", "-v", "-o", "none", "-a", record["anchor"], "-f", "-", text=record["expected"] + "\n"))
    if normalized != record["expected"]:
        raise Error("PF normalization changed the proposed permission scope")
    verify()
    if source_address(state) != record["source"] or fingerprint(pf("-s", "rules")) != record["root_hash"]:
        raise Error("source address or root PF policy changed before apply")
    if pf("-i", state["utun"], "-s", "states").strip():
        raise Error("PF states appeared before apply")
    store(directory, name, record)  # Durable intent precedes any permission change.
    try:
        reserve(record)
        current(directory, name, record)
        verify()
        pf("-o", "none", "-a", record["anchor"], "-f", "-", text=record["expected"] + "\n")
        if anchor_rules(record) != record["expected"] or fingerprint(pf("-s", "rules")) != record["root_hash"]:
            raise Error("PF apply readback or root policy changed")
        verify()
        record["phase"] = "active"
        current(directory, name, record)
        store(directory, name, record)
        return record
    except BaseException:
        revoke(directory, name, record, verify)
        raise


def revoke(directory, name, expected, verify):
    record = current(directory, name, expected)
    actual = anchor_rules(record)
    if record["phase"] == "closed":
        if actual:
            raise Error("closed PF anchor is no longer empty; refusing mutation")
        return record
    if actual and actual != record["expected"]:
        raise Error("PF anchor contents changed; refusing to flush foreign rules")
    if actual:
        current(directory, name, record)
        pf("-a", record["anchor"], "-F", "rules")
    if anchor_rules(record):
        raise Error("PF anchor could not be emptied; tunnel retained")
    record["phase"] = "rules-removed"
    current(directory, name, record)
    store(directory, name, record)
    verify()  # Never use saved interface names after ownership/lifetime loss.
    if source_address(record["session"]) != record["source"]:
        raise Error("tunnel source address changed; inspect PF states before down")
    for ip in record["session"]["routes"]:
        current(directory, name, record)
        verify()
        pf("-i", record["session"]["utun"], "-k", record["source"], "-k", ip)
    if pf("-i", record["session"]["utun"], "-s", "states").strip():
        raise Error("PF states remain on the owned interface; tunnel retained")
    verify()
    record["phase"] = "closed"
    record["states_verified"] = True
    current(directory, name, record)
    store(directory, name, record)
    return record
