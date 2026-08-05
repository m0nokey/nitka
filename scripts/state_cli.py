#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import secrets
import string
import subprocess
import sys
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from access_naming import new_share_id
from deployment_logic import (
    CASCADE_RPZ_PROFILES,
    CASCADE_RPZ_SOURCES,
    attach_cascade,
    cascade_ansible_vars,
    deployment_transport_summary,
    normalize_cascade_transport,
    replace_cascade_node,
    set_cascade_transports,
)
from nacl.public import PrivateKey
from routing_policy import import_routing_policy
from state_logic import build_port_mapping, generated_port, generated_vpn_ports
from transport_registry import validate_access_transport
from vault_schema import assert_canonical_state, sync_canonical_state

COUNTRIES_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.tsv"
SSH_USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9]{7,31}$")


def bootstrap_environment(name, default=""):
    """Read canonical bootstrap variables."""
    return os.environ.get(f"NITKA_BOOTSTRAP_{name}", default)


def ssh_proxy_username(existing):
    """Generate a portable, non-privileged SSH proxy username."""
    alphabet = string.ascii_lowercase + string.digits
    while True:
        username = secrets.choice(string.ascii_lowercase) + "".join(
            secrets.choice(alphabet) for _ in range(11)
        )
        if username not in existing and SSH_USERNAME_PATTERN.fullmatch(username):
            return username


def management_state(node):
    return node["management"]


def bootstrap_state(node):
    return node["bootstrap"]


def access_state(node):
    return node["access"]


def xray_reality_state(node):
    return access_state(node)["xray_reality"]


def ssh_proxy_state(node):
    return access_state(node)["ssh_proxy"]


def ssh_proxy_access_keys(node):
    keys = ssh_proxy_state(node).get("access_keys", [])
    return keys if isinstance(keys, list) else []


def new_ssh_proxy_access_key(existing):
    private_key, public_key = deploy_key()
    return {
        "key_id": "ssh-" + secrets.token_hex(6),
        "username": ssh_proxy_username(existing),
        "private_key": private_key,
        "authorized_key": public_key,
    }


def country_codes():
    try:
        with COUNTRIES_FILE.open(encoding="utf-8") as handle:
            return {
                line.split("\t", 1)[0].strip().lower()
                for line in handle
                if line.strip() and not line.startswith("#")
            }
    except OSError as exc:
        raise SystemExit(f"country code table is unavailable: {COUNTRIES_FILE}") from exc


def read_state():
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("encrypted Vault state is empty; refusing to modify it")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"encrypted Vault state is invalid JSON: {exc.msg}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("nodes"), dict):
        raise SystemExit("encrypted Vault state has an invalid structure; expected an object with nodes")
    try:
        assert_canonical_state(state)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Vault state must be migrated to schema v2: {exc}") from exc
    return state


def pending_operations(state):
    operations = state.setdefault("pending_operations", {})
    if not isinstance(operations, dict):
        raise SystemExit("encrypted Vault state has invalid pending operations")
    return operations


def pending_operation(state, transaction_id):
    operation = pending_operations(state).get(transaction_id)
    if not isinstance(operation, dict) or not isinstance(operation.get("node"), dict):
        raise SystemExit(f"pending installation not found: {transaction_id}")
    return operation


def ip_info(host):
    """Return display metadata without making deployment depend on a web API."""
    endpoints = (
        (f"https://ipinfo.io/{host}", "ipinfo"),
        (f"https://ipwho.is/{host}", "ipwho"),
    )
    for endpoint, service in endpoints:
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-sSfL",
                    "--connect-timeout",
                    "2",
                    "--max-time",
                    "4",
                    "--tlsv1.3",
                    "--http2",
                    "--proto",
                    "=https",
                    endpoint,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue
            data = json.loads(result.stdout)
            country = data.get("country")
            org = data.get("org")
            if service == "ipwho":
                country = data.get("country_code")
                org = data.get("connection", {}).get("org")
            if country:
                provider = (
                    org.split(" ", 1)[1]
                    if service == "ipinfo" and isinstance(org, str) and " " in org
                    else org
                )
                return country, provider or "N/A"
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue
    # These fields are only used for display. Keep deployment functional when
    # both metadata services are blocked or temporarily unavailable.
    return "N/A", "N/A"


def reality_keys():
    key = PrivateKey.generate()

    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return encode(key.encode()), encode(key.public_key.encode())


def deploy_key():
    fd, path = tempfile.mkstemp(prefix="xray-deploy-")
    os.close(fd)
    os.unlink(path)
    try:
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path], check=True)
        private = Path(path).read_text(encoding="utf-8")
        public = Path(path + ".pub").read_text(encoding="utf-8").strip()
        return private, public
    finally:
        for filename in (path, path + ".pub"):
            try:
                os.unlink(filename)
            except FileNotFoundError:
                pass


def ssh_public_key_from_private(path):
    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def ssh_public_key_fingerprint(public_key):
    result = subprocess.run(
        ["ssh-keygen", "-lf", "-", "-E", "sha256"],
        input=f"{public_key}\n",
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.split()[1]


parser = argparse.ArgumentParser()
parser.add_argument(
    "action",
    choices=(
        "count", "names", "extract", "extract-cascade", "mark-deployed",
        "set-management-key", "set-ssh-host-key", "set-ssh-transport-host-key", "set-bootstrap",
        "set-ssh-mapping", "set-management-user",
        "repair-access-port",
        "set-access-transport", "ssh-transport-info",
        "normalize-cascade-transport",
        "transport-summary",
        "set-cascade-transports",
        "capture-cascade-transport-keys",
        "set-dns-profile", "set-local-region", "begin-install", "restore-pending",
        "sync-pending", "commit-pending", "abort-pending", "pending-list",
        "remove-node", "add-node",
        "set-cascade-dns-profile", "set-cascade-country-policy",
        "add-cascade", "replace-cascade-node", "remove-cascade", "share-management-key", "add-key", "add-keys", "remove-key", "remove-all-keys",
        "add-ssh-key", "add-ssh-keys", "remove-ssh-key",
        "import-routing",
    ),
)
parser.add_argument("args", nargs="*")
parser.add_argument("--bootstrap-key")
parser.add_argument("--node-role", choices=("single", "ingress", "egress"), default="single")
parser.add_argument("--server-name", default="github.com")
parser.add_argument(
    "--access-transport",
    choices=("xray-reality", "ssh-proxy", "ssh"),
    default="xray-reality",
)
parser.add_argument(
    "--port-mode",
    choices=("random", "vision-443", "xhttp-443", "manual"),
    default="random",
)
parser.add_argument("--vision-port", type=int)
parser.add_argument("--xhttp-port", type=int)
parser.add_argument(
    "--dns-profile",
    choices=("disabled", "minimal", "optimal", "security", "full", "maximum", "custom"),
    default="disabled",
)
parser.add_argument(
    "--dns-lists",
    default="",
    help="comma-separated DNS source names for the custom profile",
)
parser.add_argument(
    "--local-region-countries",
    default="",
    help="comma-separated ISO alpha-2 country codes",
)
parser.add_argument("--cascade-local-root", default="")
opts = parser.parse_args()
state = read_state()
nodes = state.setdefault("nodes", {})

if opts.action == "count":
    print(len(nodes))
    raise SystemExit(0)
elif opts.action == "names":
    print("\n".join(nodes))
    raise SystemExit(0)
elif opts.action == "extract":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("extract requires NODE")
    node = nodes[opts.args[0]]
    management = management_state(node)
    bootstrap = bootstrap_state(node)
    access = access_state(node)
    xray = xray_reality_state(node)
    ssh_proxy = ssh_proxy_state(node)
    try:
        access_transport = validate_access_transport(access.get("transport", "xray-reality"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = {
        "access_xray_state": xray,
        "access_transport_selection": access_transport,
        "access_xray_server_name": xray.get("server_name", "github.com"),
        "access_xray_country": node.get("country", "xx"),
        "access_xray_dns_profile": xray.get("dns_filter_profile", "disabled"),
        "access_xray_dns_lists": xray.get("dns_filter_lists", []),
        "access_xray_local_region_countries": xray.get("local_region_countries", []),
        "access_ssh_proxy_external_port": ssh_proxy.get("port", 0),
        "access_ssh_proxy_authorized_key": (
            ssh_proxy_access_keys(node)[0].get("authorized_key", "")
            if ssh_proxy_access_keys(node) else ""
        ),
        "access_ssh_proxy_access_keys": [
            {
                "key_id": item.get("key_id", ""),
                "username": item.get("username", ""),
                "authorized_key": item.get("authorized_key", ""),
            }
            for item in ssh_proxy_access_keys(node)
        ],
        "access_xray_public_host": node["host"],
        "system_base_management_user": management["user"],
        "system_base_management_authorized_key": management["authorized_key"],
        "system_base_deploy_user": "deploy",
        "system_base_deploy_authorized_key": management["authorized_key"],
        "system_base_management_private_key": management["private_key"],
        "system_base_harden_ssh_initial_user": management.get("user", "deploy"),
        "system_base_harden_ssh_preserve_users": list(dict.fromkeys(
            user for user in (
                management.get("user", "deploy"),
                bootstrap.get("user"),
                *(management.get("preserved_users") or []),
            ) if user and user != "root"
        )),
        "system_base_management_sshd_port": management["sshd_port"],
        "system_base_management_port": management["port"],
        "system_base_ssh_host_public_key": management.get("host_public_key", ""),
        "system_base_ssh_host_fingerprint": management.get("host_fingerprint", ""),
    }
    json.dump(output, sys.stdout, indent=2)
    print()
    raise SystemExit(0)
elif opts.action == "mark-deployed":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("mark-deployed requires NODE")
    node = nodes[opts.args[0]]
    management = management_state(node)
    bootstrap = bootstrap_state(node)
    xray = xray_reality_state(node)
    bootstrap["private_key"] = ""
    mapping = node.get("port_mapping", {})
    nat_enabled = mapping.get("nat", {}).get("enabled")
    if nat_enabled is True:
        management_port = mapping.get("ports", {}).get("management_ssh", {}).get(
            "external", management["port"]
        )
    else:
        management_port = management["sshd_port"]
    management["port"] = management_port
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=bootstrap.get("port"),
        management_external=management_port,
        sshd_internal=management["sshd_port"],
        services={
            "xray_reality": xray.get("vision_port"),
            "xray_xhttp": xray.get("xhttp_port"),
        } if xray else {},
        source="state_cli",
    )
    node["status"] = "Active"
elif opts.action == "extract-cascade":
    if len(opts.args) != 1:
        raise SystemExit("extract-cascade requires DEPLOYMENT_ID")
    local_root = opts.cascade_local_root or str(
        Path(os.environ.get("XDG_STATE_HOME", "/tmp")) / "nitka" / "cascade"
    )
    output = cascade_ansible_vars(state, opts.args[0], local_root)
    json.dump(output, sys.stdout, indent=2)
    print()
    raise SystemExit(0)
elif opts.action == "set-management-key":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit("set-management-key requires NODE PRIVATE_KEY PUBLIC_KEY")
    node = nodes[opts.args[0]]
    management = management_state(node)
    management["private_key"] = Path(opts.args[1]).read_text(encoding="utf-8")
    management["authorized_key"] = Path(opts.args[2]).read_text(encoding="utf-8").strip()
    try:
        management["fingerprint"] = ssh_public_key_fingerprint(
            management["authorized_key"]
        )
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise SystemExit("invalid management public key") from exc
elif opts.action == "set-ssh-host-key":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit("set-ssh-host-key requires NODE PUBLIC_KEY_FILE FINGERPRINT")
    public_key = Path(opts.args[1]).read_text(encoding="utf-8").strip()
    fingerprint = opts.args[2].strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?", public_key):
        raise SystemExit("invalid SSH host public key")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/=]+", fingerprint):
        raise SystemExit("invalid SSH host fingerprint")
    management = management_state(nodes[opts.args[0]])
    management["host_public_key"] = public_key
    management["host_fingerprint"] = fingerprint
elif opts.action == "set-ssh-transport-host-key":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit(
            "set-ssh-transport-host-key requires NODE PUBLIC_KEY_FILE FINGERPRINT"
        )
    public_key = Path(opts.args[1]).read_text(encoding="utf-8").strip()
    fingerprint = opts.args[2].strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?", public_key):
        raise SystemExit("invalid SSH proxy host public key")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/=]+", fingerprint):
        raise SystemExit("invalid SSH proxy host fingerprint")
    transport = ssh_proxy_state(nodes[opts.args[0]])
    transport["host_public_key"] = public_key
    transport["host_fingerprint"] = fingerprint
elif opts.action == "set-bootstrap":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit("set-bootstrap requires NODE USER PORT")
    password = bootstrap_environment("PASSWORD")
    if not password:
        raise SystemExit("set-bootstrap requires NITKA_BOOTSTRAP_PASSWORD")
    node = nodes[opts.args[0]]
    bootstrap = bootstrap_state(node)
    management = management_state(node)
    xray = xray_reality_state(node)
    bootstrap["user"] = opts.args[1]
    bootstrap["port"] = int(opts.args[2])
    bootstrap["password"] = password
    management["port"] = bootstrap["port"]
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=bootstrap["port"],
        management_external=management["port"],
        sshd_internal=None,
        services={
            "xray_reality": xray.get("vision_port"),
            "xray_xhttp": xray.get("xhttp_port"),
        } if xray else {},
        source="state_cli",
    )
elif opts.action == "set-ssh-mapping":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit("set-ssh-mapping requires NODE EXTERNAL_PORT INTERNAL_PORT")
    node = nodes[opts.args[0]]
    external_port = int(opts.args[1])
    internal_port = int(opts.args[2])
    management = management_state(node)
    bootstrap = bootstrap_state(node)
    xray = xray_reality_state(node)
    management["port"] = external_port
    management["sshd_port"] = internal_port
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=bootstrap.get("port"),
        management_external=external_port,
        sshd_internal=internal_port,
        services={
            "xray_reality": xray.get("vision_port"),
            "xray_xhttp": xray.get("xhttp_port"),
        } if xray else {},
        source="state_cli",
    )
elif opts.action == "set-management-user":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("set-management-user requires NODE USER")
    if opts.args[1] != "deploy":
        raise SystemExit("management user must be deploy")
    node = nodes[opts.args[0]]
    management = management_state(node)
    previous = management.get("user")
    if previous and previous != "deploy":
        preserved = management.setdefault("preserved_users", [])
        if previous not in preserved:
            preserved.append(previous)
    management["user"] = "deploy"
elif opts.action == "repair-access-port":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("repair-access-port requires NODE")
    node = nodes[opts.args[0]]
    if access_state(node).get("transport") == "ssh-proxy":
        transport = ssh_proxy_state(node)
        management = management_state(node)
        bootstrap = bootstrap_state(node)
        xray = xray_reality_state(node)
        reserved = {
            value
            for value in (
                bootstrap.get("port"),
                management.get("sshd_port"),
                management.get("port"),
                xray.get("vision_port"),
                xray.get("xhttp_port"),
            )
            if isinstance(value, int)
        }
        current = transport.get("port")
        if not isinstance(current, int) or current in reserved:
            transport["port"] = generated_port(reserved)
elif opts.action == "set-access-transport":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("set-access-transport requires NODE TRANSPORT")
    try:
        transport = validate_access_transport(opts.args[1])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if transport not in ("xray-reality", "ssh-proxy"):
        raise SystemExit("standalone access transport must be xray-reality or ssh-proxy")
    node = nodes[opts.args[0]]
    access = access_state(node)
    if transport == "ssh-proxy":
        ssh_proxy = ssh_proxy_state(node)
        keys = ssh_proxy_access_keys(node)
        if not keys:
            keys.append(new_ssh_proxy_access_key(set()))
        ssh_proxy["access_keys"] = keys
        if not ssh_proxy.get("port"):
            used_ports = {
                value
                for value in (
                    bootstrap_state(node).get("port"),
                    management_state(node).get("sshd_port"),
                    management_state(node).get("port"),
                    xray_reality_state(node).get("vision_port"),
                    xray_reality_state(node).get("xhttp_port"),
                )
                if isinstance(value, int)
            }
            ssh_proxy["port"] = generated_port(used_ports)
        ssh_proxy["enabled"] = True
    access["transport"] = transport
elif opts.action == "ssh-transport-info":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("ssh-transport-info requires NODE")
    node = nodes[opts.args[0]]
    transport = ssh_proxy_state(node)
    keys = ssh_proxy_access_keys(node)
    if access_state(node).get("transport") != "ssh-proxy" or not keys:
        raise SystemExit("SSH transport is not enabled for this node")
    access_keys = []
    for key in keys:
        access_key = dict(key)
        try:
            access_key["fingerprint"] = ssh_public_key_fingerprint(
                access_key["authorized_key"]
            )
        except (KeyError, OSError, subprocess.SubprocessError, IndexError):
            access_key["fingerprint"] = ""
        access_keys.append(access_key)
    json.dump(
        {
            "host": node["host"],
            "port": transport["port"],
            "access_keys": access_keys,
            "host_fingerprint": transport.get("host_fingerprint", ""),
        },
        sys.stdout,
        indent=2,
    )
    print()
    raise SystemExit(0)
elif opts.action == "normalize-cascade-transport":
    if len(opts.args) != 1:
        raise SystemExit("normalize-cascade-transport requires DEPLOYMENT_ID")
    state = normalize_cascade_transport(state, opts.args[0])
elif opts.action == "transport-summary":
    if len(opts.args) != 1:
        raise SystemExit("transport-summary requires DEPLOYMENT_ID")
    json.dump(deployment_transport_summary(state, opts.args[0]), sys.stdout, indent=2)
    print()
    raise SystemExit(0)
elif opts.action == "set-cascade-transports":
    if len(opts.args) != 3:
        raise SystemExit(
            "set-cascade-transports requires DEPLOYMENT_ID ACCESS_TRANSPORT BACKHAUL_TRANSPORT"
        )
    state = set_cascade_transports(
        state, opts.args[0], opts.args[1], opts.args[2]
    )
elif opts.action == "capture-cascade-transport-keys":
    if len(opts.args) != 2:
        raise SystemExit(
            "capture-cascade-transport-keys requires DEPLOYMENT_ID KEY_DIRECTORY"
        )
    deployment = state.get("deployments", {}).get(opts.args[0])
    if not isinstance(deployment, dict):
        raise SystemExit(f"deployment not found: {opts.args[0]}")
    key_directory = Path(opts.args[1])
    auth_private_path = key_directory / "id_ed25519"
    host_private_path = key_directory / "ssh_host_ed25519_key"
    if not auth_private_path.is_file() or not host_private_path.is_file():
        raise SystemExit("Cascade transport keypair is incomplete")
    try:
        auth_private = auth_private_path.read_text(encoding="utf-8")
        host_private = host_private_path.read_text(encoding="utf-8")
        auth_public = ssh_public_key_from_private(auth_private_path)
        host_public = ssh_public_key_from_private(host_private_path)
        auth_fingerprint = ssh_public_key_fingerprint(auth_public)
        host_fingerprint = ssh_public_key_fingerprint(host_public)
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise SystemExit("Could not read Cascade transport keypair") from exc
    backhaul_ssh_tun = deployment.setdefault("settings", {}).setdefault("backhaul_ssh_tun", {})
    backhaul_ssh_tun.update({
        "auth_private_key": auth_private,
        "auth_public_key": auth_public,
        "auth_fingerprint": auth_fingerprint,
        "host_private_key": host_private,
        "host_public_key": host_public,
        "host_fingerprint": host_fingerprint,
    })
elif opts.action == "set-dns-profile":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("set-dns-profile requires NODE PROFILE")
    profile = opts.args[1]
    if profile not in ("disabled", "minimal", "optimal", "security", "full", "maximum", "custom"):
        raise SystemExit("unsupported DNS protection profile")
    node_xray = xray_reality_state(nodes[opts.args[0]])
    node_xray["dns_filter_profile"] = profile
    if profile == "custom":
        node_xray["dns_filter_lists"] = [item for item in opts.dns_lists.split(",") if item]
    else:
        node_xray.pop("dns_filter_lists", None)
elif opts.action == "set-cascade-dns-profile":
    if len(opts.args) != 2 or opts.args[0] not in state.get("deployments", {}):
        raise SystemExit("set-cascade-dns-profile requires DEPLOYMENT_ID PROFILE")
    profile = opts.args[1]
    if profile not in ("disabled", "minimal", "optimal", "security", "full", "maximum", "custom"):
        raise SystemExit("unsupported Cascade DNS protection profile")
    deployment = state["deployments"][opts.args[0]]
    settings = deployment.setdefault("settings", {})
    egress = settings.setdefault("topology_cascade_egress", {})
    egress["rpz_profile"] = profile
    if profile == "disabled":
        egress["rpz_sources"] = []
    else:
        names = list(dict.fromkeys(
            item.strip() for item in opts.dns_lists.split(",") if item.strip()
        ))
        if profile != "custom":
            names = list(CASCADE_RPZ_PROFILES[profile])
        catalog = {source["name"]: source for source in CASCADE_RPZ_SOURCES}
        unknown = [name for name in names if name not in catalog]
        if unknown:
            raise SystemExit(f"unsupported Cascade RPZ source: {', '.join(unknown)}")
        if not names:
            raise SystemExit("custom Cascade DNS protection requires at least one RPZ source")
        egress["rpz_sources"] = [catalog[name] for name in names]
elif opts.action == "set-cascade-country-policy":
    if len(opts.args) != 2 or opts.args[0] not in state.get("deployments", {}):
        raise SystemExit("set-cascade-country-policy requires DEPLOYMENT_ID ENABLED_OR_DISABLED")
    if opts.args[1] not in ("enabled", "disabled"):
        raise SystemExit("set-cascade-country-policy requires enabled or disabled")
    countries = []
    if opts.args[1] == "enabled":
        countries = list(dict.fromkeys(
            item.strip().lower()
            for item in opts.local_region_countries.split(",")
            if item.strip()
        ))
        invalid = [item for item in countries if not re.fullmatch(r"[a-z]{2}", item)]
        invalid.extend(item for item in countries if item not in country_codes() and item not in invalid)
        if invalid:
            raise SystemExit(f"unsupported country code: {', '.join(invalid)}")
        if not countries:
            raise SystemExit("enabled Cascade country policy requires at least one country")
    state["deployments"][opts.args[0]].setdefault("settings", {}).setdefault(
        "topology_cascade_ingress", {}
    )["local_region_countries"] = countries
elif opts.action == "set-local-region":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("set-local-region requires NODE ENABLED_OR_DISABLED")
    if opts.args[1] not in ("enabled", "disabled"):
        raise SystemExit("set-local-region requires enabled or disabled")
    countries = []
    if opts.args[1] == "enabled":
        countries = list(dict.fromkeys(
            item.strip().lower()
            for item in opts.local_region_countries.split(",")
            if item.strip()
        ))
        invalid = [item for item in countries if not re.fullmatch(r"[a-z]{2}", item)]
        invalid.extend(item for item in countries if item not in country_codes() and item not in invalid)
        if invalid:
            raise SystemExit(f"unsupported country code: {', '.join(invalid)}")
        if not countries:
            raise SystemExit("enabled local-region policy requires at least one country")
    xray_reality_state(nodes[opts.args[0]])["local_region_countries"] = countries
elif opts.action == "import-routing":
    if len(opts.args) != 2:
        raise SystemExit("import-routing requires NODE ROUTING_FILE")
    state = import_routing_policy(state, opts.args[0], opts.args[1])
elif opts.action == "begin-install":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("begin-install requires NODE TRANSACTION_ID")
    node_name, transaction_id = opts.args
    operations = pending_operations(state)
    if transaction_id in operations:
        raise SystemExit(f"pending installation already exists: {transaction_id}")
    candidate = nodes.pop(node_name)
    operations[transaction_id] = {
        "kind": "install",
        "phase": "prepared",
        "node_name": node_name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "node": candidate,
    }
elif opts.action == "restore-pending":
    if len(opts.args) != 1:
        raise SystemExit("restore-pending requires TRANSACTION_ID")
    operation = pending_operation(state, opts.args[0])
    state["nodes"][operation["node_name"]] = deepcopy(operation["node"])
elif opts.action == "sync-pending":
    if len(opts.args) != 2:
        raise SystemExit("sync-pending requires TRANSACTION_ID PHASE")
    transaction_id, phase = opts.args
    operation = pending_operation(state, transaction_id)
    node_name = operation["node_name"]
    if node_name not in nodes:
        raise SystemExit("sync-pending input does not contain the pending node")
    operation["node"] = deepcopy(nodes[node_name])
    operation["phase"] = phase
    del nodes[node_name]
elif opts.action == "commit-pending":
    if len(opts.args) != 1:
        raise SystemExit("commit-pending requires TRANSACTION_ID")
    transaction_id = opts.args[0]
    operation = pending_operation(state, transaction_id)
    state["nodes"][operation["node_name"]] = operation["node"]
    del state["pending_operations"][transaction_id]
elif opts.action == "abort-pending":
    if len(opts.args) != 1:
        raise SystemExit("abort-pending requires TRANSACTION_ID")
    transaction_id = opts.args[0]
    pending_operation(state, transaction_id)
    del state["pending_operations"][transaction_id]
elif opts.action == "pending-list":
    json.dump(
        [
            {
                "transaction_id": transaction_id,
                "kind": operation.get("kind", ""),
                "phase": operation.get("phase", ""),
                "node_name": operation.get("node_name", ""),
                "host": operation.get("node", {}).get("host", ""),
            }
            for transaction_id, operation in pending_operations(state).items()
            if isinstance(operation, dict)
        ],
        sys.stdout,
        indent=2,
    )
    print()
    raise SystemExit(0)
elif opts.action == "remove-node":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("remove-node requires NODE")
    node_name = opts.args[0]
    referenced = {
        role.get("node")
        for deployment in state.get("deployments", {}).values()
        if isinstance(deployment, dict)
        for role in deployment.get("roles", {}).values()
        if isinstance(role, dict)
    }
    if node_name in referenced:
        raise SystemExit("cannot remove a node referenced by a Cascade deployment")
    del nodes[node_name]
elif opts.action == "add-node":
    if len(opts.args) != 2:
        raise SystemExit("add-node requires NAME HOST")
    name, host = opts.args
    if name == "auto":
        base = re.sub(r"[^A-Za-z0-9]+", "-", host).strip("-").lower() or "server"
        name = "vpn-" + base[:48]
        suffix = 2
        original = name
        while name in nodes:
            name = f"{original}-{suffix}"
            suffix += 1
    if name in nodes:
        raise SystemExit(f"node already exists: {name}")
    country, provider = ip_info(host)
    private, public = deploy_key()
    bootstrap_private = ""
    bootstrap_public = ""
    bootstrap_fingerprint = ""
    if opts.bootstrap_key:
        bootstrap_private = Path(opts.bootstrap_key).read_text(encoding="utf-8")
        try:
            bootstrap_public = ssh_public_key_from_private(Path(opts.bootstrap_key))
            bootstrap_fingerprint = ssh_public_key_fingerprint(bootstrap_public)
        except (OSError, subprocess.SubprocessError, IndexError) as exc:
            raise SystemExit("invalid bootstrap private key") from exc
    bootstrap_password = bootstrap_environment("PASSWORD")
    bootstrap_port = int(bootstrap_environment("PORT", "22"))
    bootstrap_user = bootstrap_environment("USER", "root")
    used_ports = set()
    ssh_port = generated_port(used_ports)
    ssh_proxy = {}
    if opts.access_transport == "ssh-proxy":
        if opts.node_role != "single":
            raise SystemExit("SSH access transport is supported for standalone nodes only")
        ssh_access_key = new_ssh_proxy_access_key(set())
        ssh_proxy = {
            "access_keys": [ssh_access_key],
            "port": generated_port(used_ports),
            "host_public_key": "",
            "host_fingerprint": "",
            "enabled": True,
        }
        xray_state = {}
    else:
        reality_private, reality_public = reality_keys()
        server_name = opts.server_name.strip().lower()
        if (
            len(server_name) > 253
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", server_name)
            or any(len(label) > 63 for label in server_name.split("."))
        ):
            raise SystemExit("server name must be a valid ASCII hostname")
        manual_ports = None
        if opts.port_mode == "manual":
            manual_ports = (opts.vision_port, opts.xhttp_port)
        try:
            vision_port, xhttp_port = generated_vpn_ports(used_ports, opts.port_mode, manual_ports)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        vision_uuid = str(uuid.uuid4())
        xray_state = {
            "vision_port": vision_port, "xhttp_port": xhttp_port,
            "port_mode": opts.port_mode,
            "reality_private_key": reality_private,
            "reality_public_key": reality_public,
            "reality_short_id": secrets.token_hex(8),
            "server_name": server_name,
            "dns_filter_profile": opts.dns_profile,
            "dns_filter_lists": [item for item in opts.dns_lists.split(",") if item],
            "local_region_countries": [],
            "access_keys": [{
                "key_id": "key-" + vision_uuid.replace("-", "")[:8],
                "share_id": new_share_id(set()),
                "vision_uuid": vision_uuid,
                "xhttp_uuid": str(uuid.uuid4()),
            }],
        }
        if opts.node_role == "egress":
            xray_state = {}
    nodes[name] = {
        "name": name,
        "host": host,
        "country": country,
        "provider": provider,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "management": {
            "user": "deploy",
            "private_key": private,
            "authorized_key": public,
            "fingerprint": ssh_public_key_fingerprint(public),
            "sshd_port": ssh_port,
            "port": bootstrap_port,
            "host_public_key": "",
            "host_fingerprint": "",
            "preserved_users": [],
        },
        "bootstrap": {
            "user": bootstrap_user,
            "private_key": bootstrap_private,
            "public_key": bootstrap_public,
            "fingerprint": bootstrap_fingerprint,
            "password": bootstrap_password,
            "port": bootstrap_port,
        },
        "port_mapping": build_port_mapping(
            bootstrap_external=bootstrap_port,
            management_external=bootstrap_port,
            sshd_internal=None,
            services={
                "xray_reality": vision_port,
                "xray_xhttp": xhttp_port,
            } if xray_state else {},
            source="state_cli",
        ),
        "topology": {"role": opts.node_role},
        "access": {
            "transport": validate_access_transport(opts.access_transport),
            "xray_reality": xray_state,
            "ssh_proxy": ssh_proxy,
        },
    }
elif opts.action == "add-cascade":
    if len(opts.args) != 3:
        raise SystemExit("add-cascade requires DEPLOYMENT_ID INGRESS_NODE EGRESS_NODE")
    state = attach_cascade(state, opts.args[0], opts.args[1], opts.args[2])
elif opts.action == "replace-cascade-node":
    if len(opts.args) != 3:
        raise SystemExit("replace-cascade-node requires DEPLOYMENT_ID ROLE NODE")
    state = replace_cascade_node(state, opts.args[0], opts.args[1], opts.args[2])
elif opts.action == "remove-cascade":
    if len(opts.args) != 1:
        raise SystemExit("remove-cascade requires DEPLOYMENT_ID")
    deployment_id = opts.args[0]
    deployments = state.get("deployments", {})
    deployment = deployments.get(deployment_id)
    if not isinstance(deployment, dict):
        raise SystemExit(f"deployment not found: {deployment_id}")
    roles = deployment.get("roles", {})
    node_names = {
        roles.get(role, {}).get("node")
        for role in ("ingress", "egress")
        if isinstance(roles.get(role), dict)
    }
    del deployments[deployment_id]
    referenced = {
        role.get("node")
        for other in deployments.values()
        for role in other.get("roles", {}).values()
        if isinstance(role, dict)
    }
    for node_name in node_names - referenced:
        nodes.pop(node_name, None)
elif opts.action == "share-management-key":
    if len(opts.args) != 2 or opts.args[0] not in nodes or opts.args[1] not in nodes:
        raise SystemExit("share-management-key requires SOURCE_NODE DESTINATION_NODE")
    source = nodes[opts.args[0]]
    destination = nodes[opts.args[1]]
    source_management = management_state(source)
    destination_management = management_state(destination)
    destination_management["private_key"] = source_management["private_key"]
    destination_management["authorized_key"] = source_management["authorized_key"]
    destination_management["fingerprint"] = source_management.get("fingerprint", "")
elif opts.action in ("add-ssh-key", "add-ssh-keys", "remove-ssh-key"):
    if len(opts.args) < 1 or opts.args[0] not in nodes:
        raise SystemExit(f"{opts.action} requires NODE")
    node = nodes[opts.args[0]]
    if access_state(node).get("transport") != "ssh-proxy":
        raise SystemExit("SSH access keys require the standalone SSH transport")
    transport = ssh_proxy_state(node)
    keys = ssh_proxy_access_keys(node)
    if opts.action in ("add-ssh-key", "add-ssh-keys"):
        if opts.action == "add-ssh-key":
            count = 1
        elif len(opts.args) == 2 and opts.args[1].isdigit():
            count = int(opts.args[1])
            if not 1 <= count <= 50:
                raise SystemExit("SSH access key count must be between 1 and 50")
        else:
            raise SystemExit("add-ssh-keys requires NODE COUNT")
        usernames = {item.get("username") for item in keys}
        for _ in range(count):
            key = new_ssh_proxy_access_key(usernames)
            usernames.add(key["username"])
            keys.append(key)
    else:
        if len(opts.args) != 2:
            raise SystemExit("remove-ssh-key requires NODE KEY_ID")
        key_id = opts.args[1]
        if len(keys) <= 1:
            raise SystemExit("cannot delete the last SSH proxy access key")
        remaining = [key for key in keys if key.get("key_id") != key_id]
        if len(remaining) == len(keys):
            raise SystemExit(f"SSH access key not found: {key_id}")
        transport["access_keys"] = remaining
elif opts.action in ("add-key", "add-keys", "remove-key", "remove-all-keys"):
    if len(opts.args) < 1:
        raise SystemExit(f"{opts.action} requires NODE")
    node = nodes.get(opts.args[0])
    if node is None:
        raise SystemExit(f"node not found: {opts.args[0]}")
    xray = xray_reality_state(node)
    keys = xray.setdefault("access_keys", [])
    if opts.action in ("add-key", "add-keys"):
        if opts.action == "add-key":
            count = 1
        else:
            if len(opts.args) != 2 or not opts.args[1].isdigit():
                raise SystemExit("add-keys requires NODE COUNT")
            count = int(opts.args[1])
            if not 1 <= count <= 50:
                raise SystemExit("access key count must be between 1 and 50")
        share_ids = {key.get("share_id") for key in keys}
        for _ in range(count):
            vision_uuid = str(uuid.uuid4())
            keys.append({
                "key_id": "key-" + vision_uuid.replace("-", "")[:8],
                "share_id": new_share_id(share_ids),
                "vision_uuid": vision_uuid,
                "xhttp_uuid": str(uuid.uuid4()),
            })
            share_ids.add(keys[-1]["share_id"])
    elif opts.action == "remove-all-keys":
        xray["access_keys"] = []
    else:
        if len(opts.args) != 2:
            raise SystemExit("remove-key requires NODE KEY_ID")
        key_id = opts.args[1]
        xray["access_keys"] = [key for key in keys if key["key_id"] != key_id]
        if len(xray["access_keys"]) == len(keys):
            raise SystemExit(f"key not found: {key_id}")

sync_canonical_state(state)
json.dump(state, sys.stdout, indent=2)
print()
