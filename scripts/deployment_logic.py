"""Pure state helpers for deployment topology.

The existing node state remains the source of node credentials and Xray
configuration. A deployment only describes relationships between nodes and
the services selected for that deployment.
"""

import re
from copy import deepcopy

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
        "port": 22,
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
    },
    "egress": {
        "forward_servers": [
            {"address": "1.1.1.1", "tls_name": "cloudflare-dns.com"},
            {"address": "1.0.0.1", "tls_name": "cloudflare-dns.com"},
            {"address": "94.140.14.140", "tls_name": "unfiltered.adguard-dns.com"},
            {"address": "94.140.14.141", "tls_name": "unfiltered.adguard-dns.com"},
        ],
        "rpz_sources": [
            {
                "name": "urlhaus",
                "url": "https://urlhaus.abuse.ch/downloads/rpz/",
                "zonefile": "/var/lib/unbound/rpz/urlhaus.rpz",
            },
            {
                "name": "hagezi-doh",
                "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/doh.txt",
                "zonefile": "/var/lib/unbound/rpz/hagezi-doh.rpz",
            },
            {
                "name": "adguard-cname-trackers",
                "url": "https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_trackers_rpz.txt",
                "zonefile": "/var/lib/unbound/rpz/adguard-cname-trackers.rpz",
            },
            {
                "name": "adguard-cname-mail",
                "url": "https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_mail_trackers_rpz.txt",
                "zonefile": "/var/lib/unbound/rpz/adguard-cname-mail.rpz",
            },
            {
                "name": "threatfox",
                "url": "https://threatfox.abuse.ch/downloads/threatfox.rpz",
                "zonefile": "/var/lib/unbound/rpz/threatfox.rpz",
            },
            {
                "name": "hagezi-pro-plus",
                "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/pro.plus.txt",
                "zonefile": "/var/lib/unbound/rpz/hagezi-pro-plus.rpz",
            },
        ],
    },
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
        "services": {
            "ssh_tun": {"enabled": True},
            "dns": {"backend": "unbound"},
            "health": {"enabled": True},
        },
        "policy": {
            "routing": "legacy",
            "dns": "legacy",
        },
        "settings": deepcopy(CASCADE_DEFAULT_SETTINGS),
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
    validate_deployments(state)
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
    missing.extend(key for key in ("vision_uuid", "xhttp_uuid") if not access_key.get(key))
    if missing:
        raise ValueError(f"ingress Xray state is missing: {', '.join(missing)}")

    settings = deepcopy(CASCADE_DEFAULT_SETTINGS)
    settings.update(deepcopy(deployment.get("settings", {})))
    ssh_tun = settings["ssh_tun"]
    ingress_settings = settings["ingress"]
    egress_settings = settings["egress"]
    local_root = str(local_root).rstrip("/")
    remote_root = str(settings["remote_root"]).rstrip("/")
    deployment_root = f"{local_root}/{deployment_id}"

    vars_ = {
        "system_base_deploy_user": "deploy",
        "cascade_ingress_deploy_authorized_key": ingress.get("management_authorized_key", ""),
        "cascade_egress_deploy_authorized_key": egress.get("management_authorized_key", ""),
        "cascade_ingress_local_dir": f"{deployment_root}/ingress",
        "cascade_ingress_state_dir": f"{deployment_root}/ingress/state",
        "cascade_ingress_remote_dir": f"{remote_root}/ingress",
        "cascade_egress_remote_dir": f"{remote_root}/egress",
        "cascade_ssh_tun_ssh_key_dir": f"{deployment_root}/ssh",
        "cascade_ssh_tun_ssh_username": ssh_tun["username"],
        "cascade_ssh_tun_ssh_port": ssh_tun["port"],
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
        "cascade_egress_unbound_forward_servers": egress_settings["forward_servers"],
        "cascade_egress_unbound_rpz_sources": egress_settings["rpz_sources"],
    }
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
        vars_[f"cascade_ingress_xray_{name}"] = ingress_xray.get(name, [])
    vars_["cascade_ingress_xray_share_link_path"] = f"{deployment_root}/share-links.txt"
    return vars_
