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
        PLANE_BACKHAUL,
        TRANSPORT_SSH_TUN,
        TRANSPORT_XRAY_REALITY,
        get_transport_adapter,
        validate_deployment_transports,
        validate_transport_plan,
    )
except ImportError:  # pragma: no cover - direct execution through state_cli.py
    from state_logic import generated_port
    from transport_registry import (
        PLANE_BACKHAUL,
        TRANSPORT_SSH_TUN,
        TRANSPORT_XRAY_REALITY,
        get_transport_adapter,
        validate_deployment_transports,
        validate_transport_plan,
    )

SCHEMA_VERSION = 1
TOPOLOGY_CASCADE = "cascade"
ROLE_INGRESS = "ingress"
ROLE_EGRESS = "egress"
CASCADE_DEFAULT_SETTINGS = {
    "remote_root": "/opt/nitka/cascade",
    "backhaul_ssh_tun": {
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
    "topology_cascade_ingress": {
        "clash_port": 1080,
        "xray_port_min": 20000,
        "xray_port_max": 60000,
        "obfs_path": "/",
        "local_region_countries": [],
    },
    "topology_cascade_egress": {
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


def _is_dedicated_transport_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return 1025 <= port <= 65535 and port != 22


def _backhaul_ssh_tun_runtime(backhaul_ssh_tun_settings):
    """Read the canonical SSH TUN key material captured for this deployment."""
    return {
        "auth_private_key": _key_payload(backhaul_ssh_tun_settings.get("auth_private_key")),
        "auth_public_key": _key_payload(backhaul_ssh_tun_settings.get("auth_public_key")),
        "host_private_key": _key_payload(backhaul_ssh_tun_settings.get("host_private_key")),
        "host_public_key": _key_payload(backhaul_ssh_tun_settings.get("host_public_key")),
    }


def _cascade_reserved_ports(egress):
    reserved = set()
    for value in (
        egress.get("management", {}).get("port"),
        egress.get("bootstrap", {}).get("port"),
        egress.get("management", {}).get("sshd_port"),
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
    backhaul_ssh_tun_settings = settings["backhaul_ssh_tun"]
    reserved = _cascade_reserved_ports(egress)
    try:
        port = int(backhaul_ssh_tun_settings.get("port"))
    except (TypeError, ValueError):
        port = None
    if (
        port is None
        or not _is_dedicated_transport_port(port)
        or port in reserved
    ):
        backhaul_ssh_tun_settings["port"] = generated_port(reserved)
    else:
        backhaul_ssh_tun_settings["port"] = port
    deployment["settings"] = settings
    return result


def _preserved_management_users(node):
    management = node.get("management", {})
    bootstrap = node.get("bootstrap", {})
    users = [
        management.get("user"),
        bootstrap.get("user"),
        *(management.get("preserved_users") or []),
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
            ROLE_INGRESS: {"node": ingress_node},
            ROLE_EGRESS: {"node": egress_node},
        },
        "transports": {
            "access": {"transport": TRANSPORT_XRAY_REALITY},
            "backhaul": {"transport": TRANSPORT_SSH_TUN},
        },
        "services": {
            "backhaul_ssh_tun": {"enabled": True},
            "dns_unbound": {"enabled": True},
            "healthcheck": {"enabled": True},
        },
        "policy": {
            "routing": "vault",
            "dns": "vault",
        },
        "settings": _deep_merge(
            CASCADE_DEFAULT_SETTINGS,
            {"backhaul_ssh_tun": {"port": generated_port(set())}},
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


def replace_cascade_node(state, deployment_id, role, new_node):
    """Switch one Cascade role to a prepared node without changing transports."""
    if role not in (ROLE_INGRESS, ROLE_EGRESS):
        raise ValueError(f"unsupported Cascade role: {role}")

    result = _state_copy(state)
    deployments = result.get("deployments", {})
    deployment = deployments.get(deployment_id)
    if deployment is None:
        raise ValueError(f"deployment not found: {deployment_id}")
    if not isinstance(deployment, dict):
        raise TypeError(f"deployment must be an object: {deployment_id}")
    nodes = result.get("nodes", {})
    if new_node not in nodes:
        raise ValueError(f"replacement node not found: {new_node}")

    roles = deployment.get("roles", {})
    if not isinstance(roles, dict):
        raise TypeError(f"deployment roles must be an object: {deployment_id}")
    selected = roles.get(role)
    if selected is None:
        raise ValueError(f"missing deployment role: {deployment_id}/{role}")
    if not isinstance(selected, dict):
        raise TypeError(f"deployment role must be an object: {deployment_id}/{role}")
    old_node = selected.get("node")
    if old_node == new_node:
        raise ValueError("replacement node must differ from the current node")
    if old_node not in nodes:
        raise ValueError(f"current node not found: {deployment_id}/{role}")

    selected["node"] = new_node
    if role == ROLE_INGRESS:
        # Keep client links, REALITY keys, and routing policy unchanged when
        # an ingress replacement is introduced by a future UI flow.
        nodes[new_node].setdefault("access", {})["xray_reality"] = deepcopy(
            nodes[old_node].get("access", {}).get("xray_reality", {})
        )
    return result


def deployment_transport_summary(state, deployment_id):
    """Describe the access and backhaul adapters selected by a deployment.

    This is intentionally metadata-only. It lets the UI explain a replacement
    or a future transport migration without inspecting transport credentials.
    Deployment validation remains strict and refuses adapters that have no
    Ansible implementation yet.
    """
    deployment = state.get("deployments", {}).get(deployment_id)
    if deployment is None:
        raise ValueError(f"deployment not found: {deployment_id}")
    if not isinstance(deployment, dict):
        raise TypeError(f"deployment must be an object: {deployment_id}")

    selected = deployment.get("transports", {})
    if not isinstance(selected, dict):
        raise TypeError("deployment transports must be an object")
    access = selected.get("access", {}).get("transport", TRANSPORT_XRAY_REALITY)
    backhaul = selected.get("backhaul", {}).get("transport", TRANSPORT_SSH_TUN)
    access_adapter = get_transport_adapter(access, "access")
    backhaul_adapter = get_transport_adapter(backhaul, PLANE_BACKHAUL)
    return {
        "topology": deployment.get("topology", TOPOLOGY_CASCADE),
        "access": {
            "transport": access_adapter.name,
            "implemented": access_adapter.implemented,
            "implementation_role": access_adapter.implementation_role,
        },
        "backhaul": {
            "transport": backhaul_adapter.name,
            "implemented": backhaul_adapter.implemented,
            "implementation_role": backhaul_adapter.implementation_role,
        },
    }


def set_cascade_transports(state, deployment_id, access_transport, backhaul_transport):
    """Return state with a validated Cascade transport plan.

    Callers should deploy the returned state and persist it only after both
    endpoints pass healthchecks. This keeps transport migration transactional
    in the same way as node replacement.
    """
    result = _state_copy(state)
    deployment = result.get("deployments", {}).get(deployment_id)
    if deployment is None:
        raise ValueError(f"deployment not found: {deployment_id}")
    if not isinstance(deployment, dict):
        raise TypeError(f"deployment must be an object: {deployment_id}")
    plan = validate_transport_plan(
        TOPOLOGY_CASCADE, access_transport, backhaul_transport
    )
    selected = deployment.setdefault("transports", {})
    selected["access"] = {"transport": plan["bindings"][0]["transport"]}
    selected["backhaul"] = {"transport": plan["bindings"][1]["transport"]}
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
        validate_deployment_transports(deployment)
        for role in (ROLE_INGRESS, ROLE_EGRESS):
            selected = roles.get(role)
            if not isinstance(selected, dict):
                raise TypeError(f"missing deployment role: {deployment_id}/{role}")
            node = selected.get("node")
            if node not in state["nodes"]:
                raise ValueError(f"deployment node not found: {deployment_id}/{role}")

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
    ingress_xray = ingress.get("access", {}).get("xray_reality")
    if not isinstance(ingress_xray, dict):
        raise TypeError("ingress node must contain access.xray_reality")
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
    backhaul_ssh_tun_settings = settings["backhaul_ssh_tun"]
    backhaul_ssh_tun_runtime = _backhaul_ssh_tun_runtime(backhaul_ssh_tun_settings)
    try:
        backhaul_ssh_tun_settings["port"] = int(backhaul_ssh_tun_settings["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("cascade SSH TUN requires a valid dedicated transport port") from exc
    if not 1025 <= backhaul_ssh_tun_settings["port"] <= 65535 or backhaul_ssh_tun_settings["port"] == 22:
        raise ValueError(
            "cascade SSH TUN requires a dedicated external transport port; "
            "management/bootstrap port 22 cannot be used"
        )
    reserved_ports = _cascade_reserved_ports(egress)
    if backhaul_ssh_tun_settings["port"] in reserved_ports:
        raise ValueError(
            "cascade SSH TUN transport port must differ from all egress "
            "bootstrap/management SSH ports"
        )
    ingress_settings = settings["topology_cascade_ingress"]
    egress_settings = settings["topology_cascade_egress"]
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

    if "transports" in deployment:
        transport_plan = validate_deployment_transports(deployment)
    else:
        transport_plan = validate_transport_plan(
            TOPOLOGY_CASCADE,
            TRANSPORT_XRAY_REALITY,
            TRANSPORT_SSH_TUN,
        )
    access_transport = next(
        binding["transport"]
        for binding in transport_plan["bindings"]
        if binding["node_role"] == ROLE_INGRESS
        and binding["plane"] == "access"
    )
    backhaul_transport = next(
        binding["transport"]
        for binding in transport_plan["bindings"]
        if binding["node_role"] == ROLE_INGRESS
        and binding["plane"] == "backhaul"
    )

    ansible_variables = {
        "topology_cascade_transport_bindings": transport_plan["bindings"],
        "topology_cascade_ingress_access_transport": access_transport,
        "topology_cascade_ingress_backhaul_transport": backhaul_transport,
        "topology_cascade_egress_backhaul_transport": backhaul_transport,
        "system_base_deploy_user": "deploy",
        "topology_cascade_ingress_management_sshd_port": ingress.get("management", {}).get("sshd_port", 22),
        "topology_cascade_egress_management_sshd_port": egress.get("management", {}).get("sshd_port", 22),
        "topology_cascade_ingress_management_port": ingress.get("management", {}).get("port", 22),
        "topology_cascade_egress_management_port": egress.get("management", {}).get("port", 22),
        "topology_cascade_ingress_harden_ssh_initial_user": ingress.get("management", {}).get(
            "user", "deploy"
        ),
        "topology_cascade_egress_harden_ssh_initial_user": egress.get("management", {}).get(
            "user", "deploy"
        ),
        "topology_cascade_ingress_harden_ssh_preserve_users": _preserved_management_users(ingress),
        "topology_cascade_egress_harden_ssh_preserve_users": _preserved_management_users(egress),
        "topology_cascade_ingress_deploy_authorized_key": ingress.get("management", {}).get("authorized_key", ""),
        "topology_cascade_egress_deploy_authorized_key": egress.get("management", {}).get("authorized_key", ""),
        "topology_cascade_ingress_local_dir": f"{deployment_root}/ingress",
        "topology_cascade_ingress_state_dir": f"{deployment_root}/ingress/state",
        "topology_cascade_ingress_remote_dir": f"{remote_root}/ingress",
        "topology_cascade_egress_remote_dir": f"{remote_root}/egress",
        "backhaul_ssh_tun_ssh_key_dir": f"{deployment_root}/ssh",
        "backhaul_ssh_tun_ssh_username": backhaul_ssh_tun_settings["username"],
        "backhaul_ssh_tun_container_port": backhaul_ssh_tun_settings["internal_port"],
        "backhaul_ssh_tun_public_host": egress["host"],
        "backhaul_ssh_tun_network_container_subnet_cidr_ipv4": backhaul_ssh_tun_settings["container_subnet"],
        "backhaul_ssh_tun_network_vpn_subnet_cidr_ipv4": backhaul_ssh_tun_settings["vpn_subnet"],
        "backhaul_ssh_tun_network_interface": backhaul_ssh_tun_settings["interface"],
        "backhaul_ssh_tun_device_number": backhaul_ssh_tun_settings["device_number"],
        "backhaul_ssh_tun_network_container_gateway_ipv4": backhaul_ssh_tun_settings["container_gateway"],
        "backhaul_ssh_tun_network_container_vpn_ipv4": backhaul_ssh_tun_settings["container_vpn"],
        "backhaul_ssh_tun_network_vpn_gateway_ipv4": backhaul_ssh_tun_settings["vpn_gateway"],
        "backhaul_ssh_tun_network_vpn_gateway_cidr_ipv4": backhaul_ssh_tun_settings["vpn_gateway_cidr"],
        "backhaul_ssh_tun_network_vpn_client_cidr_ipv4": backhaul_ssh_tun_settings["vpn_client_cidr"],
        "access_xray_public_host": ingress["host"],
        "access_xray_xhttp_port": ingress_xray["xhttp_port"],
        "access_xray_reality_port": ingress_xray["vision_port"],
        "access_xray_access_keys": access_keys,
        "access_xray_xhttp_uuid": access_key["xhttp_uuid"],
        "access_xray_reality_uuid": access_key["vision_uuid"],
        "access_xray_reality_private_key": ingress_xray["reality_private_key"],
        "access_xray_reality_public_key": ingress_xray["reality_public_key"],
        "access_xray_reality_short_id": ingress_xray["reality_short_id"],
        "access_xray_obfs_host": ingress_xray["server_name"],
        "access_xray_obfs_path": ingress_settings["obfs_path"],
        "access_xray_xhttp_port_min": ingress_settings["xray_port_min"],
        "access_xray_xhttp_port_max": ingress_settings["xray_port_max"],
        "access_xray_reality_port_min": ingress_settings["xray_port_min"],
        "access_xray_reality_port_max": ingress_settings["xray_port_max"],
        "access_xray_xhttp_remarks": f"{deployment_id}-xhttp",
        "access_xray_reality_remarks": f"{deployment_id}-vision",
        "access_xray_share_link_path": f"{deployment_root}/share-links.txt",
        "topology_cascade_ingress_clash_port": ingress_settings["clash_port"],
        "topology_cascade_ingress_clash_tcp_concurrent": ingress_xray.get(
            "clash_tcp_concurrent", True
        ),
        # Resolve all Cascade DNS through the egress Unbound instance.  This
        # prevents direct resolvers and public fallbacks from bypassing RPZ.
        "access_xray_dns_egress_only": True,
        "access_xray_local_region_countries": local_region_countries,
        "topology_cascade_egress_unbound_forward_servers": egress_settings["forward_servers"],
        "topology_cascade_egress_unbound_rpz_sources": rpz_sources,
    }
    backhaul_adapter = get_transport_adapter(backhaul_transport, PLANE_BACKHAUL)
    if not all(
        (
            backhaul_adapter.client_service_name,
            backhaul_adapter.server_service_name,
            backhaul_adapter.client_container_name,
            backhaul_adapter.server_container_name,
        )
    ):
        raise ValueError(
            f"backhaul adapter has no runtime service contract: {backhaul_transport}"
        )
    ansible_variables.update(
        {
            "backhaul_ssh_tun_client_service_name": backhaul_adapter.client_service_name,
            "backhaul_ssh_tun_client_container_name": backhaul_adapter.client_container_name,
            "backhaul_ssh_tun_server_service_name": backhaul_adapter.server_service_name,
            "backhaul_ssh_tun_server_container_name": backhaul_adapter.server_container_name,
        }
    )
    ansible_variables["backhaul_ssh_tun_ssh_port"] = backhaul_ssh_tun_settings["port"]
    for name, value in (
        ("backhaul_ssh_tun_auth_private_key", backhaul_ssh_tun_runtime["auth_private_key"]),
        ("backhaul_ssh_tun_auth_public_key", backhaul_ssh_tun_runtime["auth_public_key"]),
        ("backhaul_ssh_tun_host_private_key", backhaul_ssh_tun_runtime["host_private_key"]),
        ("backhaul_ssh_tun_host_public_key", backhaul_ssh_tun_runtime["host_public_key"]),
    ):
        if value:
            ansible_variables[name] = value
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
        ansible_variables[f"access_xray_{name}"] = ingress_xray.get(
            name, routing.get(name, [])
        )
    policy = ingress_xray.get("routing_policy", {})
    if isinstance(policy, dict):
        ansible_variables["access_xray_routing_policy_sha256"] = policy.get(
            "source_sha256", ""
        )
    ansible_variables["access_xray_share_link_path"] = f"{deployment_root}/share-links.txt"
    return ansible_variables
