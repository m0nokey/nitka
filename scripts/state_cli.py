#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nacl.public import PrivateKey
from deployment_logic import (
    CASCADE_DEFAULT_SETTINGS,
    CASCADE_RPZ_PROFILES,
    CASCADE_RPZ_SOURCES,
    attach_cascade,
    cascade_ansible_vars,
    normalize_cascade_transport,
)
from routing_policy import import_routing_policy
from state_logic import build_port_mapping, generated_port, generated_vpn_ports
from transport_registry import validate_access_transport

COUNTRIES_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.tsv"


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
    return state


def ip_info(host):
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["curl", "-sSfL", "--tlsv1.3", "--http2", "--proto", "=https",
                 f"https://ipinfo.io/{host}"], capture_output=True, text=True, timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                org = data.get("org", "N/A")
                return data.get("country", "N/A"), org.split(" ", 1)[1] if " " in org else org
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        if attempt < 2:
            import time
            time.sleep(3)
    raise SystemExit("Failed to get IP info")


def reality_keys():
    key = PrivateKey.generate()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
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
        "set-management-key", "set-ssh-host-key", "set-bootstrap",
        "set-ssh-mapping", "set-management-user",
        "set-cascade-transport-port",
        "normalize-cascade-transport",
        "capture-cascade-transport-keys",
        "set-dns-profile", "set-local-region", "remove-node", "add-node",
        "set-cascade-dns-profile", "set-cascade-country-policy",
        "add-cascade", "remove-cascade", "share-management-key", "add-key", "add-keys", "remove-key", "remove-all-keys",
        "import-routing",
    ),
)
parser.add_argument("args", nargs="*")
parser.add_argument("--bootstrap-key")
parser.add_argument("--node-role", choices=("single", "ingress", "egress"), default="single")
parser.add_argument("--server-name", default="github.com")
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
    try:
        access_transport = validate_access_transport(
            node.get("access_transport", "xray-reality")
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = {
        "xray_state": node["xray"],
        "xray_access_transport": access_transport,
        "xray_server_name": node["xray"].get("server_name", "github.com"),
        "xray_dns_profile": node["xray"].get("dns_filter_profile", "disabled"),
        "xray_dns_lists": node["xray"].get("dns_filter_lists", []),
        "xray_local_region_countries": node["xray"].get("local_region_countries", []),
        "xray_public_host": node["host"],
        "management_user": node["management_user"],
        "management_authorized_key": node["management_authorized_key"],
        "system_base_deploy_user": "deploy",
        "system_base_deploy_authorized_key": node["management_authorized_key"],
        "management_private_key": node["management_private_key"],
        "system_base_harden_ssh_initial_user": node.get("management_user", "deploy"),
        "system_base_harden_ssh_preserve_users": list(dict.fromkeys(
            user for user in (
                node.get("management_user", "deploy"),
                node.get("bootstrap_user"),
                *(node.get("preserved_management_users") or []),
            ) if user and user != "root"
        )),
        "ssh_port": node["ssh_port"],
        "management_port": node["management_port"],
        "system_base_ssh_host_public_key": node.get("ssh_host_public_key", ""),
        "system_base_ssh_host_fingerprint": node.get("ssh_host_fingerprint", ""),
    }
    json.dump(output, sys.stdout, indent=2)
    print()
    raise SystemExit(0)
elif opts.action == "mark-deployed":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("mark-deployed requires NODE")
    node = nodes[opts.args[0]]
    node["bootstrap_private_key"] = ""
    mapping = node.get("port_mapping", {})
    nat_enabled = mapping.get("nat", {}).get("enabled")
    if nat_enabled is True:
        management_port = mapping.get("ports", {}).get("management_ssh", {}).get(
            "external", node["management_port"]
        )
    else:
        management_port = node["ssh_port"]
    node["management_port"] = management_port
    xray = node.get("xray", {})
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=node.get("bootstrap_ssh_port"),
        management_external=management_port,
        sshd_internal=node["ssh_port"],
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
    node["management_private_key"] = Path(opts.args[1]).read_text(encoding="utf-8")
    node["management_authorized_key"] = Path(opts.args[2]).read_text(encoding="utf-8").strip()
    try:
        node["management_fingerprint"] = ssh_public_key_fingerprint(
            node["management_authorized_key"]
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
    node = nodes[opts.args[0]]
    node["ssh_host_public_key"] = public_key
    node["ssh_host_fingerprint"] = fingerprint
elif opts.action == "set-bootstrap":
    if len(opts.args) != 3 or opts.args[0] not in nodes:
        raise SystemExit("set-bootstrap requires NODE USER PORT")
    password = os.environ.get("XRAY_BOOTSTRAP_PASSWORD", "")
    if not password:
        raise SystemExit("set-bootstrap requires XRAY_BOOTSTRAP_PASSWORD")
    node = nodes[opts.args[0]]
    node["bootstrap_user"] = opts.args[1]
    node["bootstrap_ssh_port"] = int(opts.args[2])
    node["bootstrap_password"] = password
    node["management_port"] = node["bootstrap_ssh_port"]
    xray = node.get("xray", {})
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=node["bootstrap_ssh_port"],
        management_external=node["management_port"],
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
    node["management_port"] = external_port
    node["ssh_port"] = internal_port
    xray = node.get("xray", {})
    node["port_mapping"] = build_port_mapping(
        bootstrap_external=node.get("bootstrap_ssh_port"),
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
    previous = node.get("management_user")
    if previous and previous != "deploy":
        preserved = node.setdefault("preserved_management_users", [])
        if previous not in preserved:
            preserved.append(previous)
    node["management_user"] = "deploy"
elif opts.action == "set-cascade-transport-port":
    if len(opts.args) != 2:
        raise SystemExit("set-cascade-transport-port requires DEPLOYMENT_ID PORT")
    deployment = state.get("deployments", {}).get(opts.args[0])
    if not isinstance(deployment, dict):
        raise SystemExit(f"deployment not found: {opts.args[0]}")
    try:
        transport_port = int(opts.args[1])
    except ValueError as exc:
        raise SystemExit("cascade transport port must be a number") from exc
    if not 1025 <= transport_port <= 65535 or transport_port == 22:
        raise SystemExit(
            "cascade transport port must be between 1025 and 65535 and cannot be 22"
        )
    deployment.setdefault("settings", {}).setdefault("ssh_tun", {})[
        "port"
    ] = transport_port
elif opts.action == "normalize-cascade-transport":
    if len(opts.args) != 1:
        raise SystemExit("normalize-cascade-transport requires DEPLOYMENT_ID")
    state = normalize_cascade_transport(state, opts.args[0])
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
    ssh_tun = deployment.setdefault("settings", {}).setdefault("ssh_tun", {})
    ssh_tun.update({
        "ssh_tun_private_key": auth_private,
        "ssh_tun_public_key": auth_public,
        "ssh_tun_fingerprint": auth_fingerprint,
        "ssh_tun_host_private_key": host_private,
        "ssh_tun_host_public_key": host_public,
        "ssh_tun_host_fingerprint": host_fingerprint,
    })
elif opts.action == "set-dns-profile":
    if len(opts.args) != 2 or opts.args[0] not in nodes:
        raise SystemExit("set-dns-profile requires NODE PROFILE")
    profile = opts.args[1]
    if profile not in ("disabled", "minimal", "optimal", "security", "full", "maximum", "custom"):
        raise SystemExit("unsupported DNS protection profile")
    node_xray = nodes[opts.args[0]].setdefault("xray", {})
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
    egress = settings.setdefault("egress", {})
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
        "ingress", {}
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
    nodes[opts.args[0]].setdefault("xray", {})["local_region_countries"] = countries
elif opts.action == "import-routing":
    if len(opts.args) != 2:
        raise SystemExit("import-routing requires NODE ROUTING_FILE")
    state = import_routing_policy(state, opts.args[0], opts.args[1])
elif opts.action == "remove-node":
    if len(opts.args) != 1 or opts.args[0] not in nodes:
        raise SystemExit("remove-node requires NODE")
    del nodes[opts.args[0]]
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
    bootstrap_password = os.environ.get("XRAY_BOOTSTRAP_PASSWORD", "")
    bootstrap_port = int(os.environ.get("XRAY_BOOTSTRAP_PORT", "22"))
    bootstrap_user = os.environ.get("XRAY_BOOTSTRAP_USER", "root")
    reality_private, reality_public = reality_keys()
    server_name = opts.server_name.strip().lower()
    if (
        len(server_name) > 253
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", server_name)
        or any(len(label) > 63 for label in server_name.split("."))
    ):
        raise SystemExit("server name must be a valid ASCII hostname")
    used_ports = set()
    ssh_port = generated_port(used_ports)
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
        "management_user": "deploy",
        "management_private_key": private,
        "management_authorized_key": public,
        "management_fingerprint": ssh_public_key_fingerprint(public),
        "bootstrap_private_key": bootstrap_private,
        "bootstrap_public_key": bootstrap_public,
        "bootstrap_fingerprint": bootstrap_fingerprint,
        "bootstrap_password": bootstrap_password,
        "bootstrap_user": bootstrap_user,
        "bootstrap_ssh_port": bootstrap_port,
        "ssh_port": ssh_port,
        "management_port": bootstrap_port,
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
        "role": opts.node_role,
        "access_transport": "xray-reality",
        "ssh_host_public_key": "",
        "ssh_host_fingerprint": "",
        "xray": xray_state,
    }
elif opts.action == "add-cascade":
    if len(opts.args) != 3:
        raise SystemExit("add-cascade requires DEPLOYMENT_ID INGRESS_NODE EGRESS_NODE")
    state = attach_cascade(state, opts.args[0], opts.args[1], opts.args[2])
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
    destination["management_private_key"] = source["management_private_key"]
    destination["management_authorized_key"] = source["management_authorized_key"]
    destination["management_fingerprint"] = source.get("management_fingerprint", "")
elif opts.action in ("add-key", "add-keys", "remove-key", "remove-all-keys"):
    if len(opts.args) < 1:
        raise SystemExit(f"{opts.action} requires NODE")
    node = nodes.get(opts.args[0])
    if node is None:
        raise SystemExit(f"node not found: {opts.args[0]}")
    keys = node.setdefault("xray", {}).setdefault("access_keys", [])
    if opts.action in ("add-key", "add-keys"):
        if opts.action == "add-key":
            count = 1
        else:
            if len(opts.args) != 2 or not opts.args[1].isdigit():
                raise SystemExit("add-keys requires NODE COUNT")
            count = int(opts.args[1])
            if not 1 <= count <= 50:
                raise SystemExit("access key count must be between 1 and 50")
        for _ in range(count):
            vision_uuid = str(uuid.uuid4())
            keys.append({
                "key_id": "key-" + vision_uuid.replace("-", "")[:8],
                "vision_uuid": vision_uuid,
                "xhttp_uuid": str(uuid.uuid4()),
            })
    elif opts.action == "remove-all-keys":
        node["xray"]["access_keys"] = []
    else:
        if len(opts.args) != 2:
            raise SystemExit("remove-key requires NODE KEY_ID")
        key_id = opts.args[1]
        node["xray"]["access_keys"] = [key for key in keys if key["key_id"] != key_id]
        if len(node["xray"]["access_keys"]) == len(keys):
            raise SystemExit(f"key not found: {key_id}")

json.dump(state, sys.stdout, indent=2)
print()
