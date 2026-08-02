"""Pure state helpers for deployment topology.

The existing node state remains the source of node credentials and Xray
configuration. A deployment only describes relationships between nodes and
the services selected for that deployment.
"""

from copy import deepcopy
import re


SCHEMA_VERSION = 1
TOPOLOGY_CASCADE = "cascade"
ROLE_INGRESS = "ingress"
ROLE_EGRESS = "egress"
BACKEND_XRAY = "xray"
TRANSPORT_EXISTING_XRAY = "existing-xray"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _state_copy(state):
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    if not isinstance(state.get("nodes"), dict):
        raise ValueError("state must contain a nodes object")
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
    }


def attach_cascade(state, deployment_id, ingress_node, egress_node):
    """Return a new state with a cascade, leaving the input untouched."""
    result = _state_copy(state)
    deployments = result.setdefault("deployments", {})
    if not isinstance(deployments, dict):
        raise ValueError("state deployments must be an object")
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
        raise ValueError("state must contain a nodes object")
    deployments = state.get("deployments", {})
    if not isinstance(deployments, dict):
        raise ValueError("state deployments must be an object")

    for deployment_id, deployment in deployments.items():
        _validate_identifier(deployment_id, "deployment id")
        if not isinstance(deployment, dict):
            raise ValueError(f"deployment must be an object: {deployment_id}")
        if deployment.get("topology") != TOPOLOGY_CASCADE:
            raise ValueError(f"unsupported deployment topology: {deployment_id}")
        roles = deployment.get("roles")
        if not isinstance(roles, dict):
            raise ValueError(f"deployment roles must be an object: {deployment_id}")
        for role in (ROLE_INGRESS, ROLE_EGRESS):
            selected = roles.get(role)
            if not isinstance(selected, dict):
                raise ValueError(f"missing deployment role: {deployment_id}/{role}")
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
