"""Pure state helpers for deployment topology.

The existing node state remains the source of node credentials and Xray
configuration. A deployment only describes relationships between nodes and
the services selected for that deployment.
"""

import base64
import re
from copy import deepcopy

try:
    from .state_logic import generated_port
    from .transport_registry import (
        TRANSPORT_SSH_TUN,
        TRANSPORT_XRAY_REALITY,
        validate_deployment_transports,
        validate_transport_plan,
    )
except ImportError:  # pragma: no cover - direct execution through state_cli.py
    from state_logic import generated_port
    from transport_registry import (
        TRANSPORT_SSH_TUN,
        TRANSPORT_XRAY_REALITY,
        validate_deployment_transports,
        validate_transport_plan,
    )

SCHEMA_VERSION = 1
TOPOLOGY_CASCADE = "cascade"
ROLE_INGRESS = "ingress"
ROLE_EGRESS = "egress"
BACKEND_XRAY = "xray"
TRANSPORT_EXISTING_XRAY = "existing-xray"

CASCADE_DEFAULT_SETTINGS = {
    "remote_root": "/opt/nitka/cascade",
    "ssh_tun": {
        "username": "vpnuser",
        "port": None,
        "internal_port": 22,
        "container_subnet": "172.29.113.0/24",
        "vpn_subnet": "10.11.12.0/30",
        "interface": "tun10",
        "device_number": 10,
        "container_gateway": "172.29.113.1",
        "container_vpn": "172.29.113.2",
        "vpn_gateway": "10.11.12.1",
        "vpn_gateway_cidr": "10.11.12.1/30",
        "vpn_client_cidr": "10.11.12.2/30",
    },
    "ingress": {
        "clash_port": 1080,
        "xray_port_min": 20000,
        "xray_port_max": 60000,
        "obfs_path": "/",
        "local_region_countries": [],
    },
    "egress": {
        "forward_servers": [
            {"address": "1.1.1.1", "tls_name": "cloudflare-dns.com"},
            {"address": "1.0.0.1", "tls_name": "cloudflare-dns.com"},
            {"address": "94.140.14.140", "tls_name": "unfiltered.adguard-dns.com"},
            {"address": "94.140.14.141", "tls_name": "unfiltered.adguard-dns.com"},
        ],
        "rpz_profile": "security",
        "rpz_sources": [
            {
                "name": "urlhaus",
                "url": "https://urlhaus.abuse.ch/downloads/rpz/",
                "zonefile": "/var/lib/unbound/rpz/urlhaus.rpz",
            },
            {
                "name": "hagezi-tif-mini",
                "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/tif.mini.txt",
                "zonefile": "/var/lib/unbound/rpz/hagezi-tif-mini.rpz",
            },
            {
                "name": "threatfox",
                "url": "https://threatfox.abuse.ch/downloads/threatfox.rpz",
                "zonefile": "/var/lib/unbound/rpz/threatfox.rpz",
            },
            {
                "name": "hagezi-dyndns",
                "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/dyndns.txt",
                "zonefile": "/var/lib/unbound/rpz/hagezi-dyndns.rpz",
            },
        ],
    },
}

# Keep the Cascade selector aligned with the standalone Xray-TUI DNS catalog.
# New Cascades use the security-only profile by default. Broad advertising and
# tracker lists remain opt-in profiles.
CASCADE_RPZ_SOURCES = [
    {"name": "urlhaus", "url": "https://urlhaus.abuse.ch/downloads/rpz/", "zonefile": "/var/lib/unbound/rpz/urlhaus.rpz"},
    {"name": "hagezi-tif-mini", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/tif.mini.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-tif-mini.rpz"},
    {"name": "hagezi-doh", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/doh.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-doh.rpz"},
    {"name": "hagezi-bypass", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/doh-vpn-proxy-bypass.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-bypass.rpz"},
    {"name": "adguard-cname-trackers", "url": "https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_trackers_rpz.txt", "zonefile": "/var/lib/unbound/rpz/adguard-cname-trackers.rpz"},
    {"name": "adguard-cname-mail", "url": "https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_mail_trackers_rpz.txt", "zonefile": "/var/lib/unbound/rpz/adguard-cname-mail.rpz"},
    {"name": "threatfox", "url": "https://threatfox.abuse.ch/downloads/threatfox.rpz", "zonefile": "/var/lib/unbound/rpz/threatfox.rpz"},
    {"name": "hagezi-pro-plus", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/pro.plus.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-pro-plus.rpz"},
    {"name": "hagezi-ultimate", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/ultimate.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-ultimate.rpz"},
    {"name": "hagezi-tif-medium", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/tif.medium.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-tif-medium.rpz"},
    {"name": "hagezi-tif-ips", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/tif-ips.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-tif-ips.rpz"},
    {"name": "hagezi-dyndns", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/dyndns.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-dyndns.rpz"},
    {"name": "hagezi-spam-tlds", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/spam-tlds-rpz.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-spam-tlds.rpz"},
    {"name": "hagezi-popup-ads", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/popupads.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-popup-ads.rpz"},
    {"name": "hagezi-nsfw", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/nsfw.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-nsfw.rpz"},
    {"name": "hagezi-gambling-mini", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/gambling.mini.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-gambling-mini.rpz"},
    {"name": "hagezi-gambling-medium", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/gambling.medium.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-gambling-medium.rpz"},
    {"name": "hagezi-gambling-full", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/gambling.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-gambling-full.rpz"},
    {"name": "hagezi-social", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/social.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-social.rpz"},
    {"name": "hagezi-safesearch", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/nosafesearch.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-safesearch.rpz"},
    {"name": "hagezi-anti-piracy", "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/anti.piracy.txt", "zonefile": "/var/lib/unbound/rpz/hagezi-anti-piracy.rpz"},
    {"name": "cascade-local-ads-tracking", "zonefile": "/var/lib/unbound/rpz/cascade-ads-tracking.rpz"},
]

CASCADE_RPZ_PROFILES = {
    "disabled": [],
    "minimal": ["urlhaus"],
    "optimal": ["urlhaus", "hagezi-tif-mini"],
    "security": ["urlhaus", "hagezi-tif-mini", "threatfox", "hagezi-dyndns"],
    "full": [
        "urlhaus", "hagezi-doh", "adguard-cname-trackers",
        "adguard-cname-mail", "threatfox", "hagezi-pro-plus",
        "cascade-local-ads-tracking",
    ],
    "maximum": [
        "urlhaus", "hagezi-bypass", "adguard-cname-trackers",
        "adguard-cname-mail", "threatfox", "hagezi-ultimate",
        "hagezi-tif-medium", "hagezi-tif-ips", "hagezi-dyndns",
        "hagezi-spam-tlds",
        "cascade-local-ads-tracking",
    ],
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _state_copy(state):
    if not isinstance(state, dict):
        raise TypeError("state must be an object")
    if not isinstance(state.get("nodes"), dict):
        raise TypeError("state must contain a nodes object")
    return deepcopy(state)


def _validate_identifier(value, label):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a valid state identifier")


def _deep_merge(base, override):
    """Merge nested deployment settings without losing default siblings."""
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _first_nested_value(value, names):
    """Find a non-empty legacy field without exposing its value."""
    if isinstance(value, dict):
        for name in names:
            candidate = value.get(name)
            if candidate not in (None, ""):
                return candidate
        for child in value.values():
            candidate = _first_nested_value(child, names)
            if candidate not in (None, ""):
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _first_nested_value(child, names)
            if candidate not in (None, ""):
                return candidate
    return None


def _key_payload(value):
    """Normalize a migrated plaintext or base64-encoded key payload."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if "PRIVATE KEY" in value or value.startswith("ssh-"):
        return value
    try:
        decoded = base64.b64decode(value, validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return value
    return decoded.strip() if "PRIVATE KEY" in decoded or decoded.startswith("ssh-") else value


def _decoded_project_env(state):
    payload = state.get("project_env_b64") if isinstance(state, dict) else None
    if not isinstance(payload, str) or not payload:
        return {}
    try:
        text = base64.b64decode(payload, validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return {}
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            values[key.strip()] = value.strip()
    return values


def _is_dedicated_transport_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return 1025 <= port <= 65535 and port != 22


def _legacy_ssh_tun_values(state, deployment, egress, ssh_tun):
    """Read the old Nitka transport fields while keeping them out of UI output."""
    project_env = _decoded_project_env(state)
    sources = [ssh_tun, deployment, egress, state, project_env]
    port = _first_nested_value(
        sources,
        (
            "ssh_tun_port",
            "ssh_tun_ssh_port",
            "tun_port",
            "ssh_tun_external_port",
            "transport_port",
        ),
    )
    if port in (None, "") or not _is_dedicated_transport_port(port):
        fallback_sources = [project_env, state, egress, deployment, ssh_tun]
        for source in fallback_sources:
            candidate = _first_nested_value(source, (
                "ssh_tun_port",
                "ssh_tun_ssh_port",
                "tun_port",
                "ssh_tun_external_port",
                "transport_port",
            ))
            if _is_dedicated_transport_port(candidate):
                port = candidate
                break
        if port in (None, "") or not _is_dedicated_transport_port(port):
            port = None
        for source in sources:
            if not isinstance(source, dict):
                continue
            mapping = source.get("port_mapping", {})
            ports = mapping.get("ports", {}) if isinstance(mapping, dict) else {}
            for name in ("ssh_tun", "ssh_tun_server", "tun"):
                entry = ports.get(name, {}) if isinstance(ports, dict) else {}
                if (
                    isinstance(entry, dict)
                    and _is_dedicated_transport_port(entry.get("external"))
                ):
                    port = entry["external"]
                    break
            if port not in (None, ""):
                break
    values = {
        "port": port,
        "username": _first_nested_value(
            sources,
            ("ssh_tun_username", "ssh_tun_ssh_username", "tun_username"),
        ),
        "auth_private_key": _first_nested_value(
            sources,
            ("ssh_tun_private_key", "ssh_tun_private_key_b64"),
        ),
        "auth_public_key": _first_nested_value(
            sources,
            ("ssh_tun_public_key",),
        ),
        "host_private_key": _first_nested_value(
            sources,
            ("ssh_tun_host_private_key", "ssh_tun_host_private_key_b64"),
        ),
        "host_public_key": _first_nested_value(
            sources,
            ("ssh_tun_host_public_key",),
        ),
    }
    if values["port"] not in (None, ""):
        ssh_tun["port"] = int(values["port"])
    if values["username"] not in (None, ""):
        ssh_tun["username"] = str(values["username"])
    for key in ("auth_private_key", "auth_public_key", "host_private_key", "host_public_key"):
        values[key] = _key_payload(values[key])
    for label, private_name, public_name in (
        ("SSH TUN authentication", "auth_private_key", "auth_public_key"),
        ("SSH TUN host", "host_private_key", "host_public_key"),
    ):
        if bool(values[private_name]) != bool(values[public_name]):
            raise ValueError(f"incomplete migrated {label} key pair")
    return values


def _cascade_reserved_ports(egress):
    reserved = set()
    for value in (
        egress.get("management_port"),
        egress.get("bootstrap_ssh_port"),
        egress.get("ssh_port"),
    ):
        try:
            reserved.add(int(value))
        except (TypeError, ValueError):
            continue
    return reserved


def normalize_cascade_transport(state, deployment_id):
    """Ensure a Cascade has a dedicated external SSH TUN port.

    Legacy state may contain the internal SSH port (22) or no transport port
    at all. A fresh dedicated port is safe for a new stack and is persisted by
    the caller only after the complete Cascade deployment succeeds.
    """
    result = _state_copy(state)
    validate_deployments(result)
    deployment = result["deployments"].get(deployment_id)
    if deployment is None:
        raise ValueError(f"deployment not found: {deployment_id}")
    egress_node = deployment["roles"][ROLE_EGRESS]["node"]
    egress = result["nodes"][egress_node]
    settings = _deep_merge(CASCADE_DEFAULT_SETTINGS, deployment.get("settings", {}))
    ssh_tun = settings["ssh_tun"]
    transport = _legacy_ssh_tun_values(result, deployment, egress, ssh_tun)
    reserved = _cascade_reserved_ports(egress)
    try:
        port = int(ssh_tun.get("port"))
    except (TypeError, ValueError):
        port = None
    if (
        port is None
        or not _is_dedicated_transport_port(port)
        or port in reserved
    ):
        ssh_tun["port"] = generated_port(reserved)
    else:
        ssh_tun["port"] = port
    deployment["settings"] = settings
    return result


def _preserved_management_users(node):
    users = [
        node.get("management_user"),
        node.get("bootstrap_user"),
        *(node.get("preserved_management_users") or []),
    ]
    return list(dict.fromkeys(user for user in users if user and user != "root"))


def cascade_deployment(deployment_id, ingress_node, egress_node):
    """Build the first supported cascade definition.

    Only the current Xray implementation is enabled today. Backend and
    transport are explicit so a future adapter can be selected without
    changing the topology model.
    """
    _validate_identifier(deployment_id, "deployment id")
    _validate_identifier(ingress_node, "ingress node")
    _validate_identifier(egress_node, "egress node")
    if ingress_node == egress_node:
        raise ValueError("ingress and egress must be different nodes")

    return {
        "id": deployment_id,
        "topology": TOPOLOGY_CASCADE,
        "roles": {
            ROLE_INGRESS: {
                "node": ingress_node,
                "backend": BACKEND_XRAY,
                "transport": TRANSPORT_EXISTING_XRAY,
            },
            ROLE_EGRESS: {
                "node": egress_node,
                "backend": BACKEND_XRAY,
                "transport": TRANSPORT_EXISTING_XRAY,
            },
        },
        "transports": {
            "access": {"transport": TRANSPORT_XRAY_REALITY},
            "backhaul": {"transport": TRANSPORT_SSH_TUN},
        },
        "services": {
            "ssh_tun": {"enabled": True},
            "dns": {"backend": "unbound"},
            "health": {"enabled": True},
        },
        "policy": {
            "routing": "vault",
            "dns": "vault",
        },
        "settings": _deep_merge(
            CASCADE_DEFAULT_SETTINGS,
            {"ssh_tun": {"port": generated_port(set())}},
        ),
    }


def attach_cascade(state, deployment_id, ingress_node, egress_node):
    """Return a new state with a cascade, leaving the input untouched."""
    result = _state_copy(state)
    deployments = result.setdefault("deployments", {})
    if not isinstance(deployments, dict):
        raise TypeError("state deployments must be an object")
    if deployment_id in deployments:
        raise ValueError(f"deployment already exists: {deployment_id}")
    if ingress_node not in result["nodes"]:
        raise ValueError(f"ingress node not found: {ingress_node}")
    if egress_node not in result["nodes"]:
        raise ValueError(f"egress node not found: {egress_node}")

    result["schema_version"] = max(int(result.get("schema_version", 0)), SCHEMA_VERSION)
    deployments[deployment_id] = cascade_deployment(
        deployment_id, ingress_node, egress_node
    )
    return result


def validate_deployments(state):
    """Validate deployment references without inspecting secret values."""
    if not isinstance(state, dict) or not isinstance(state.get("nodes"), dict):
        raise TypeError("state must contain a nodes object")
    deployments = state.get("deployments", {})
    if not isinstance(deployments, dict):
        raise TypeError("state deployments must be an object")

    for deployment_id, deployment in deployments.items():
        _validate_identifier(deployment_id, "deployment id")
        if not isinstance(deployment, dict):
            raise TypeError(f"deployment must be an object: {deployment_id}")
        if deployment.get("topology") != TOPOLOGY_CASCADE:
            raise ValueError(f"unsupported deployment topology: {deployment_id}")
        roles = deployment.get("roles")
        if not isinstance(roles, dict):
            raise TypeError(f"deployment roles must be an object: {deployment_id}")
        if "transports" in deployment:
            validate_deployment_transports(deployment)
        else:
            # Read old state written before the modular transport contract.
            # It remains valid while the new block is introduced gradually.
            validate_transport_plan(
                deployment.get("topology"),
                TRANSPORT_XRAY_REALITY,
                TRANSPORT_SSH_TUN
                if deployment.get("services", {}).get("ssh_tun", {}).get("enabled")
                else None,
            )
        for role in (ROLE_INGRESS, ROLE_EGRESS):
            selected = roles.get(role)
            if not isinstance(selected, dict):
                raise TypeError(f"missing deployment role: {deployment_id}/{role}")
            node = selected.get("node")
            if node not in state["nodes"]:
                raise ValueError(f"deployment node not found: {deployment_id}/{role}")
            if selected.get("backend") != BACKEND_XRAY:
                raise ValueError(f"unsupported backend: {deployment_id}/{role}")
            if selected.get("transport") != TRANSPORT_EXISTING_XRAY:
                raise ValueError(f"unsupported transport: {deployment_id}/{role}")

        if roles[ROLE_INGRESS]["node"] == roles[ROLE_EGRESS]["node"]:
            raise ValueError(f"cascade roles reference the same node: {deployment_id}")

    return True


def cascade_ansible_vars(state, deployment_id, local_root):
    """Build role variables for the two-node cascade playbooks.

    Node credentials stay in the node records. This mapper exposes only the
    values needed by Ansible and keeps the role-specific names explicit.
    """
    state = normalize_cascade_transport(state, deployment_id)
    deployments = state.get("deployments", {})
    deployment = deployments.get(deployment_id)
    if deployment is None:
        raise ValueError(f"deployment not found: {deployment_id}")

    roles = deployment["roles"]
    ingress = state["nodes"][roles[ROLE_INGRESS]["node"]]
    egress = state["nodes"][roles[ROLE_EGRESS]["node"]]
    ingress_xray = ingress.get("xray")
    if not isinstance(ingress_xray, dict):
        raise TypeError("ingress node must contain an xray object")
    access_keys = ingress_xray.get("access_keys")
    if not isinstance(access_keys, list) or not access_keys:
        raise ValueError("ingress node must contain at least one Xray access key")
    access_key = access_keys[0]
    required_xray = (
        "vision_port",
        "xhttp_port",
        "reality_private_key",
        "reality_public_key",
        "reality_short_id",
        "server_name",
    )
    missing = [key for key in required_xray if not ingress_xray.get(key)]
    for index, key in enumerate(access_keys, 1):
        if not isinstance(key, dict):
            raise TypeError(f"ingress Xray access key must be an object: {index}")
        missing.extend(
            f"access_keys[{index}].{field}"
            for field in ("vision_uuid", "xhttp_uuid")
            if not key.get(field)
        )
    if missing:
        raise ValueError(f"ingress Xray state is missing: {', '.join(missing)}")

    settings = _deep_merge(CASCADE_DEFAULT_SETTINGS, deployment.get("settings", {}))
    ssh_tun = settings["ssh_tun"]
    transport = _legacy_ssh_tun_values(state, deployment, egress, ssh_tun)
    try:
        ssh_tun["port"] = int(ssh_tun["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("cascade SSH TUN requires a valid dedicated transport port") from exc
    if not 1025 <= ssh_tun["port"] <= 65535 or ssh_tun["port"] == 22:
        raise ValueError(
            "cascade SSH TUN requires a dedicated external transport port; "
            "management/bootstrap port 22 cannot be used"
        )
    reserved_ports = _cascade_reserved_ports(egress)
    if ssh_tun["port"] in reserved_ports:
        raise ValueError(
            "cascade SSH TUN transport port must differ from all egress "
            "bootstrap/management SSH ports"
        )
    ingress_settings = settings["ingress"]
    egress_settings = settings["egress"]
    rpz_sources = list(egress_settings["rpz_sources"])
    rpz_profile = egress_settings.get("rpz_profile")
    local_rpz_name = "cascade-local-ads-tracking"
    # Existing Cascade state may predate the repository-managed local RPZ.
    # Keep Full/Maximum deployments backward-compatible by enabling it during
    # rendering without writing the repository policy into the Vault.
    if rpz_profile in (None, "full", "maximum") and rpz_sources and not any(
        source.get("name") == local_rpz_name
        for source in rpz_sources
        if isinstance(source, dict)
    ):
        rpz_sources.append({
            "name": local_rpz_name,
            "zonefile": "/var/lib/unbound/rpz/cascade-ads-tracking.rpz",
        })
    local_region_countries = ingress_settings.get("local_region_countries", [])
    if not isinstance(local_region_countries, list):
        raise TypeError("Cascade country policy must be a list")
    invalid_countries = [
        country for country in local_region_countries
        if not isinstance(country, str) or not re.fullmatch(r"[a-z]{2}", country)
    ]
    if invalid_countries:
        raise ValueError(
            "Cascade country policy contains invalid ISO-3166 codes: "
            + ", ".join(map(str, invalid_countries))
        )
    local_root = str(local_root).rstrip("/")
    remote_root = str(settings["remote_root"]).rstrip("/")
    deployment_root = f"{local_root}/{deployment_id}"

    vars_ = {
        "system_base_deploy_user": "deploy",
        "cascade_ingress_harden_ssh_initial_user": ingress.get(
            "management_user", "deploy"
        ),
        "cascade_egress_harden_ssh_initial_user": egress.get(
            "management_user", "deploy"
        ),
        "cascade_ingress_harden_ssh_preserve_users": _preserved_management_users(ingress),
        "cascade_egress_harden_ssh_preserve_users": _preserved_management_users(egress),
        "cascade_ingress_deploy_authorized_key": ingress.get("management_authorized_key", ""),
        "cascade_egress_deploy_authorized_key": egress.get("management_authorized_key", ""),
        "cascade_ingress_local_dir": f"{deployment_root}/ingress",
        "cascade_ingress_state_dir": f"{deployment_root}/ingress/state",
        "cascade_ingress_remote_dir": f"{remote_root}/ingress",
        "cascade_egress_remote_dir": f"{remote_root}/egress",
        "cascade_ssh_tun_ssh_key_dir": f"{deployment_root}/ssh",
        "cascade_ssh_tun_ssh_username": ssh_tun["username"],
        "cascade_ssh_tun_container_port": ssh_tun["internal_port"],
        "cascade_ssh_tun_public_host": egress["host"],
        "cascade_ssh_tun_network_container_subnet_cidr_ipv4": ssh_tun["container_subnet"],
        "cascade_ssh_tun_network_vpn_subnet_cidr_ipv4": ssh_tun["vpn_subnet"],
        "cascade_ssh_tun_network_interface": ssh_tun["interface"],
        "cascade_ssh_tun_device_number": ssh_tun["device_number"],
        "cascade_ssh_tun_network_container_gateway_ipv4": ssh_tun["container_gateway"],
        "cascade_ssh_tun_network_container_vpn_ipv4": ssh_tun["container_vpn"],
        "cascade_ssh_tun_network_vpn_gateway_ipv4": ssh_tun["vpn_gateway"],
        "cascade_ssh_tun_network_vpn_gateway_cidr_ipv4": ssh_tun["vpn_gateway_cidr"],
        "cascade_ssh_tun_network_vpn_client_cidr_ipv4": ssh_tun["vpn_client_cidr"],
        "cascade_ingress_xray_public_host": ingress["host"],
        "cascade_ingress_xray_xhttp_port": ingress_xray["xhttp_port"],
        "cascade_ingress_xray_reality_port": ingress_xray["vision_port"],
        "cascade_ingress_xray_access_keys": access_keys,
        "cascade_ingress_xray_xhttp_uuid": access_key["xhttp_uuid"],
        "cascade_ingress_xray_reality_uuid": access_key["vision_uuid"],
        "cascade_ingress_xray_reality_private_key": ingress_xray["reality_private_key"],
        "cascade_ingress_xray_reality_public_key": ingress_xray["reality_public_key"],
        "cascade_ingress_xray_reality_short_id": ingress_xray["reality_short_id"],
        "cascade_ingress_xray_obfs_host": ingress_xray["server_name"],
        "cascade_ingress_xray_obfs_path": ingress_settings["obfs_path"],
        "cascade_ingress_xray_xhttp_port_min": ingress_settings["xray_port_min"],
        "cascade_ingress_xray_xhttp_port_max": ingress_settings["xray_port_max"],
        "cascade_ingress_xray_reality_port_min": ingress_settings["xray_port_min"],
        "cascade_ingress_xray_reality_port_max": ingress_settings["xray_port_max"],
        "cascade_ingress_xray_xhttp_remarks": f"{deployment_id}-xhttp",
        "cascade_ingress_xray_reality_remarks": f"{deployment_id}-vision",
        "cascade_ingress_xray_share_link_path": f"{deployment_root}/share-links.txt",
        "cascade_ingress_clash_port": ingress_settings["clash_port"],
        "cascade_ingress_clash_tcp_concurrent": ingress_xray.get(
            "clash_tcp_concurrent", True
        ),
        # Resolve all Cascade DNS through the egress Unbound instance.  This
        # prevents direct resolvers and public fallbacks from bypassing RPZ.
        "cascade_ingress_xray_dns_egress_only": True,
        "cascade_ingress_xray_local_region_countries": local_region_countries,
        "cascade_egress_unbound_forward_servers": egress_settings["forward_servers"],
        "cascade_egress_unbound_rpz_sources": rpz_sources,
    }
    vars_["cascade_ssh_tun_ssh_port"] = ssh_tun["port"]
    for name, value in (
        ("cascade_ssh_tun_auth_private_key", transport["auth_private_key"]),
        ("cascade_ssh_tun_auth_public_key", transport["auth_public_key"]),
        ("cascade_ssh_tun_host_private_key", transport["host_private_key"]),
        ("cascade_ssh_tun_host_public_key", transport["host_public_key"]),
    ):
        if value:
            vars_[name] = value
    for name in (
        "block_domains",
        "block_ips",
        "reality_direct_domains",
        "reality_direct_ips",
        "xhttp_direct_domains",
        "xhttp_direct_ips",
        "reality_proxy_domains",
        "reality_proxy_ips",
        "dns_direct_domains",
        "dns_direct_servers",
    ):
        routing = ingress_xray.get("routing_policy", {}).get("effective", {})
        vars_[f"cascade_ingress_xray_{name}"] = ingress_xray.get(
            name, routing.get(name, [])
        )
    policy = ingress_xray.get("routing_policy", {})
    if isinstance(policy, dict):
        vars_["cascade_ingress_xray_routing_policy_sha256"] = policy.get(
            "source_sha256", ""
        )
    vars_["cascade_ingress_xray_share_link_path"] = f"{deployment_root}/share-links.txt"
    return vars_
