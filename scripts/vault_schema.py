"""Canonical Nitka Vault v2 schema and one-way migration helpers.

Vault v2 stores node data in explicit namespaces.  Legacy flat node fields are
accepted only while migrating an old Vault and are removed from the returned
state.  Runtime code must therefore never need a compatibility view.
"""

from __future__ import annotations

from copy import deepcopy

try:
    from .access_naming import (
        LINK_TOPOLOGY_CASCADE,
        LINK_TOPOLOGY_CASCADE_REVERSE,
        SHARE_ID_PATTERN,
        legacy_share_id,
    )
except ImportError:  # pragma: no cover - direct execution through state_cli.py
    from access_naming import (
        LINK_TOPOLOGY_CASCADE,
        LINK_TOPOLOGY_CASCADE_REVERSE,
        SHARE_ID_PATTERN,
        legacy_share_id,
    )

VAULT_SCHEMA_VERSION = 2

# These are the only transport selections that existed before deployments
# stored an explicit transport plan.  They are used solely for one-way
# migration of old Cascade records; new deployments always write the block
# themselves.
DEFAULT_CASCADE_ACCESS_TRANSPORT = "xray-reality"
DEFAULT_CASCADE_BACKHAUL_TRANSPORT = "ssh-tun"

_LEGACY_NODE_FIELDS = {
    "management_user",
    "management_private_key",
    "management_authorized_key",
    "management_fingerprint",
    "ssh_port",
    "management_port",
    "ssh_host_public_key",
    "ssh_host_fingerprint",
    "bootstrap_user",
    "bootstrap_private_key",
    "bootstrap_public_key",
    "bootstrap_fingerprint",
    "bootstrap_password",
    "bootstrap_ssh_port",
    "access_transport",
    "xray",
    "ssh_transport",
    "role",
    "preserved_management_users",
}

_LEGACY_DEPLOYMENT_FIELDS = {
    "access_transport",
    "backhaul_transport",
    "transport",
    "backend",
}


def canonical_access_transport(value):
    """Normalize transport identifiers at the Vault boundary."""
    if value == "ssh":
        return "ssh-proxy"
    if value == "existing-xray":
        return "xray-reality"
    return value


def _namespace(value):
    return deepcopy(value) if isinstance(value, dict) else {}


def _copy_if_missing(target, source, target_key, source_key):
    if target.get(target_key) in (None, "") and source.get(source_key) not in (None, ""):
        target[target_key] = deepcopy(source[source_key])


def _transport_selection(value, default):
    """Return a transport object from old string or new object data."""
    if isinstance(value, dict):
        transport = value.get("transport")
        if transport not in (None, ""):
            selected = deepcopy(value)
            selected["transport"] = canonical_access_transport(transport)
            return selected
    elif value not in (None, ""):
        return {"transport": canonical_access_transport(value)}
    return {"transport": default}


def migrate_deployment(deployment: dict) -> dict:
    """Convert a legacy or partial Cascade record to the v2 contract."""
    if not isinstance(deployment, dict):
        return deployment

    roles = deployment.get("roles")
    if (
        deployment.get("topology") in (None, "")
        and isinstance(roles, dict)
        and all(isinstance(roles.get(role), dict) for role in ("ingress", "egress"))
    ):
        deployment["topology"] = "cascade"

    if deployment.get("topology") == "cascade":
        deployment.setdefault("topology_variant", LINK_TOPOLOGY_CASCADE)
        selected = deployment.get("transports")
        selected = selected if isinstance(selected, dict) else {}

        old_access = deployment.pop("access_transport", None)
        old_backhaul = deployment.pop("backhaul_transport", None)
        old_transport = deployment.pop("transport", None)
        deployment.pop("backend", None)

        # Some early records used one top-level transport field for access.
        if old_access in (None, ""):
            old_access = old_transport
        selected["access"] = _transport_selection(
            selected.get("access", old_access),
            DEFAULT_CASCADE_ACCESS_TRANSPORT,
        )
        selected["backhaul"] = _transport_selection(
            selected.get("backhaul", old_backhaul),
            DEFAULT_CASCADE_BACKHAUL_TRANSPORT,
        )
        deployment["transports"] = selected
    else:
        for field in _LEGACY_DEPLOYMENT_FIELDS:
            deployment.pop(field, None)

    return deployment


def migrate_node(node: dict) -> dict:
    """Convert one legacy or mixed node to the canonical v2 shape."""
    if not isinstance(node, dict):
        return node

    legacy_management = node
    management = _namespace(node.get("management"))
    for canonical_key, legacy_key in (
        ("user", "management_user"),
        ("private_key", "management_private_key"),
        ("authorized_key", "management_authorized_key"),
        ("fingerprint", "management_fingerprint"),
        ("sshd_port", "ssh_port"),
        ("port", "management_port"),
        ("host_public_key", "ssh_host_public_key"),
        ("host_fingerprint", "ssh_host_fingerprint"),
    ):
        _copy_if_missing(management, legacy_management, canonical_key, legacy_key)
    if "preserved_users" not in management:
        management["preserved_users"] = deepcopy(
            node.get("preserved_management_users", [])
        )
    if not isinstance(management["preserved_users"], list):
        management["preserved_users"] = []

    legacy_bootstrap = node
    bootstrap = _namespace(node.get("bootstrap"))
    for canonical_key, legacy_key in (
        ("user", "bootstrap_user"),
        ("private_key", "bootstrap_private_key"),
        ("public_key", "bootstrap_public_key"),
        ("fingerprint", "bootstrap_fingerprint"),
        ("password", "bootstrap_password"),
        ("port", "bootstrap_ssh_port"),
    ):
        _copy_if_missing(bootstrap, legacy_bootstrap, canonical_key, legacy_key)

    legacy_access = node
    access = _namespace(node.get("access"))
    if access.get("transport") in (None, ""):
        access["transport"] = canonical_access_transport(
            legacy_access.get("access_transport", "xray-reality")
        )
    else:
        access["transport"] = canonical_access_transport(access["transport"])
    if not isinstance(access.get("xray_reality"), dict):
        access["xray_reality"] = _namespace(legacy_access.get("xray"))
    if not isinstance(access.get("ssh_proxy"), dict):
        access["ssh_proxy"] = _namespace(legacy_access.get("ssh_transport"))

    xray_reality = access["xray_reality"]
    xray_keys = xray_reality.get("access_keys")
    if isinstance(xray_keys, list):
        used_share_ids = set()
        for key in xray_keys:
            if not isinstance(key, dict):
                continue
            share_id = key.get("share_id")
            if (
                not isinstance(share_id, str)
                or not SHARE_ID_PATTERN.fullmatch(share_id)
                or share_id in used_share_ids
            ):
                key["share_id"] = legacy_share_id(key, used_share_ids)
                share_id = key["share_id"]
            used_share_ids.add(share_id)

    ssh_proxy = access["ssh_proxy"]
    if "access_keys" not in ssh_proxy and isinstance(ssh_proxy.get("keys"), list):
        ssh_proxy["access_keys"] = deepcopy(ssh_proxy.pop("keys"))
    if not isinstance(ssh_proxy.get("access_keys"), list):
        ssh_proxy["access_keys"] = []
    if not ssh_proxy["access_keys"] and ssh_proxy.get("private_key") and ssh_proxy.get("authorized_key"):
        ssh_proxy["access_keys"] = [{
            "key_id": "ssh-key-legacy",
            "username": ssh_proxy.get("username", "user"),
            "private_key": ssh_proxy["private_key"],
            "authorized_key": ssh_proxy["authorized_key"],
        }]
    for field in ("private_key", "authorized_key", "username", "keys"):
        ssh_proxy.pop(field, None)

    legacy_topology = node
    topology = _namespace(node.get("topology"))
    if topology.get("role") in (None, "") and legacy_topology.get("role") not in (None, ""):
        topology["role"] = deepcopy(legacy_topology["role"])

    for field in _LEGACY_NODE_FIELDS:
        node.pop(field, None)
    node["management"] = management
    node["bootstrap"] = bootstrap
    node["access"] = access
    node["topology"] = topology
    return node


def migrate_state(state: dict) -> dict:
    """Convert a complete state to v2, idempotently and without legacy fields."""
    if not isinstance(state, dict):
        return state
    state["vault_schema_version"] = VAULT_SCHEMA_VERSION
    for node in state.get("nodes", {}).values():
        migrate_node(node)
    for operation in state.get("pending_operations", {}).values():
        if isinstance(operation, dict) and isinstance(operation.get("node"), dict):
            migrate_node(operation["node"])
    deployments = state.get("deployments", {})
    if not isinstance(deployments, dict):
        return state
    for deployment in deployments.values():
        if not isinstance(deployment, dict):
            continue
        migrate_deployment(deployment)
        settings = deployment.setdefault("settings", {})
        if "backhaul_ssh_tun" not in settings and isinstance(settings.get("ssh_tun"), dict):
            settings["backhaul_ssh_tun"] = deepcopy(settings.pop("ssh_tun"))
        if "topology_cascade_ingress" not in settings and isinstance(settings.get("ingress"), dict):
            settings["topology_cascade_ingress"] = deepcopy(settings.pop("ingress"))
        if "topology_cascade_egress" not in settings and isinstance(settings.get("egress"), dict):
            settings["topology_cascade_egress"] = deepcopy(settings.pop("egress"))
        backhaul = settings.get("backhaul_ssh_tun")
        if isinstance(backhaul, dict):
            for canonical_key, old_key in (
                ("auth_private_key", "ssh_tun_private_key"),
                ("auth_public_key", "ssh_tun_public_key"),
                ("host_private_key", "ssh_tun_host_private_key"),
                ("host_public_key", "ssh_tun_host_public_key"),
            ):
                _copy_if_missing(backhaul, backhaul, canonical_key, old_key)
                backhaul.pop(old_key, None)
        services = deployment.get("services")
        if isinstance(services, dict):
            if "backhaul_ssh_tun" not in services and "ssh_tun" in services:
                services["backhaul_ssh_tun"] = deepcopy(services["ssh_tun"])
            if "dns_unbound" not in services and "dns" in services:
                services["dns_unbound"] = deepcopy(services["dns"])
            if "healthcheck" not in services and "health" in services:
                services["healthcheck"] = deepcopy(services["health"])
            for old_key in ("ssh_tun", "dns", "health"):
                services.pop(old_key, None)
        for role in deployment.get("roles", {}).values():
            if isinstance(role, dict):
                role.pop("backend", None)
                role.pop("transport", None)
    return state


def sync_canonical_state(state: dict) -> dict:
    """Validate an already migrated state before writing it."""
    try:
        assert_canonical_state(state)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc
    return state


def assert_canonical_state(state: dict):
    """Reject a pre-v2 state at every runtime read/write boundary."""
    if not isinstance(state, dict):
        raise TypeError("Vault state must be an object")
    if state.get("vault_schema_version") != VAULT_SCHEMA_VERSION:
        raise ValueError("Vault state is not schema v2")
    if not isinstance(state.get("nodes"), dict):
        raise TypeError("Vault state is missing nodes")
    pending_operations = state.get("pending_operations", {})
    if not isinstance(pending_operations, dict):
        raise TypeError("Vault pending operations must be an object")
    for node in state.get("nodes", {}).values():
        assert_canonical_node(node)
    for operation in pending_operations.values():
        if isinstance(operation, dict) and isinstance(operation.get("node"), dict):
            assert_canonical_node(operation["node"])
    deployments = state.get("deployments", {})
    if not isinstance(deployments, dict):
        raise TypeError("Vault deployments must be an object")
    for deployment in deployments.values():
        if not isinstance(deployment, dict):
            raise TypeError("Vault deployment must be an object")
        if deployment.get("topology") != "cascade":
            raise ValueError("Vault deployment has an unsupported topology")
        topology_variant = deployment.get("topology_variant", LINK_TOPOLOGY_CASCADE)
        if topology_variant not in (LINK_TOPOLOGY_CASCADE, LINK_TOPOLOGY_CASCADE_REVERSE):
            raise ValueError("Vault deployment has an unsupported cascade topology variant")
        roles = deployment.get("roles")
        if not isinstance(roles, dict):
            raise TypeError("Vault deployment roles must be an object")
        if not all(isinstance(roles.get(role), dict) for role in ("ingress", "egress")):
            raise ValueError("Vault deployment must contain ingress and egress roles")
        transports = deployment.get("transports")
        if not isinstance(transports, dict):
            raise TypeError("Vault deployment must contain a transports object")
        for plane in ("access", "backhaul"):
            selected = transports.get(plane)
            if not isinstance(selected, dict) or not isinstance(selected.get("transport"), str):
                raise TypeError(f"Vault deployment transports.{plane} is invalid")
        # Keep schema validation strict without importing transport metadata at
        # module load time.  This also rejects an unimplemented adapter before
        # it can reach Ansible.
        try:
            try:
                from transport_registry import validate_deployment_transports
            except ModuleNotFoundError:
                from scripts.transport_registry import validate_deployment_transports

            validate_deployment_transports(deployment)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Vault deployment transport plan is invalid: {exc}") from exc
        settings = deployment.get("settings", {})
        if not isinstance(settings, dict):
            raise TypeError("Vault deployment settings must be an object")
        if any(key in settings for key in ("ssh_tun", "ingress", "egress")):
            raise ValueError("Vault deployment contains legacy settings")
        services = deployment.get("services", {})
        if not isinstance(services, dict):
            raise TypeError("Vault deployment services must be an object")
        if any(key in services for key in ("ssh_tun", "dns", "health")):
            raise ValueError("Vault deployment contains legacy services")
        for role in deployment.get("roles", {}).values():
            if isinstance(role, dict) and ("backend" in role or "transport" in role):
                raise ValueError("Vault deployment contains legacy role fields")
        if _LEGACY_DEPLOYMENT_FIELDS.intersection(deployment):
            raise ValueError("Vault deployment contains legacy transport fields")


def assert_canonical_node(node: dict):
    """Reject legacy fields so new code cannot silently reintroduce them."""
    legacy = sorted(_LEGACY_NODE_FIELDS.intersection(node))
    if legacy:
        raise ValueError(f"Vault v2 node contains legacy fields: {', '.join(legacy)}")
    for namespace in ("management", "bootstrap", "access", "topology"):
        if not isinstance(node.get(namespace), dict):
            raise TypeError(f"Vault v2 node is missing namespace: {namespace}")

    xray_reality = node["access"].get("xray_reality", {})
    if not isinstance(xray_reality, dict):
        raise TypeError("Vault v2 access.xray_reality must be an object")
    access_keys = xray_reality.get("access_keys", [])
    if not isinstance(access_keys, list):
        raise TypeError("Vault v2 access.xray_reality.access_keys must be a list")
    share_ids = set()
    for key in access_keys:
        if not isinstance(key, dict):
            raise TypeError("Vault v2 Xray access key must be an object")
        share_id = key.get("share_id")
        if not isinstance(share_id, str) or not SHARE_ID_PATTERN.fullmatch(share_id):
            raise ValueError("Vault v2 Xray access key is missing a valid share_id")
        if share_id in share_ids:
            raise ValueError(f"Vault v2 Xray access keys contain duplicate share_id: {share_id}")
        share_ids.add(share_id)
