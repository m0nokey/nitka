"""Transport adapter registry and topology transport plan.

This module is deliberately pure. It describes which adapter belongs to which
network plane and validates the deployment contract before Ansible variables
are rendered. Concrete Ansible roles remain separate from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass

TOPOLOGY_STANDALONE = "standalone"
TOPOLOGY_CASCADE = "cascade"
PLANE_ACCESS = "access"
PLANE_BACKHAUL = "backhaul"

TRANSPORT_OPERATIONS = (
    "deploy",
    "verify",
    "restart",
    "rollback_install",
    "rollback_update",
    "remove",
)

TRANSPORT_XRAY_REALITY = "xray-reality"
TRANSPORT_SSH_PROXY = "ssh-proxy"
TRANSPORT_SSH_TUN = "ssh-tun"
TRANSPORT_NAIVEPROXY = "naiveproxy"
TRANSPORT_HYSTERIA2 = "hysteria2"
TRANSPORT_WIREGUARD = "wireguard"


@dataclass(frozen=True)
class TransportAdapter:
    """Metadata for one transport adapter, not its runtime configuration."""

    name: str
    plane: str
    implementation_role: str
    supported_topologies: tuple[str, ...]
    implemented: bool
    client_service_name: str | None = None
    server_service_name: str | None = None
    client_container_name: str | None = None
    server_container_name: str | None = None
    lifecycle_operations: tuple[str, ...] = TRANSPORT_OPERATIONS


ACCESS_ADAPTERS = {
    TRANSPORT_XRAY_REALITY: TransportAdapter(
        name=TRANSPORT_XRAY_REALITY,
        plane=PLANE_ACCESS,
        implementation_role="transports/access/xray_reality",
        supported_topologies=(TOPOLOGY_STANDALONE, TOPOLOGY_CASCADE),
        implemented=True,
    ),
    TRANSPORT_SSH_PROXY: TransportAdapter(
        name=TRANSPORT_SSH_PROXY,
        plane=PLANE_ACCESS,
        implementation_role="transports/access/ssh_proxy",
        supported_topologies=(TOPOLOGY_STANDALONE,),
        implemented=True,
    ),
    TRANSPORT_NAIVEPROXY: TransportAdapter(
        name=TRANSPORT_NAIVEPROXY,
        plane=PLANE_ACCESS,
        implementation_role="transports/access/naiveproxy",
        supported_topologies=(TOPOLOGY_STANDALONE, TOPOLOGY_CASCADE),
        implemented=False,
    ),
    TRANSPORT_HYSTERIA2: TransportAdapter(
        name=TRANSPORT_HYSTERIA2,
        plane=PLANE_ACCESS,
        implementation_role="transports/access/hysteria2",
        supported_topologies=(TOPOLOGY_STANDALONE, TOPOLOGY_CASCADE),
        implemented=False,
    ),
}

BACKHAUL_ADAPTERS = {
    TRANSPORT_SSH_TUN: TransportAdapter(
        name=TRANSPORT_SSH_TUN,
        plane=PLANE_BACKHAUL,
        implementation_role="transports/backhaul/ssh_tun",
        supported_topologies=(TOPOLOGY_CASCADE,),
        implemented=True,
        client_service_name="ssh_tun_client",
        server_service_name="ssh_tun_server",
        client_container_name="cascade-ssh-tun-client",
        server_container_name="cascade-ssh-tun-server",
    ),
    TRANSPORT_NAIVEPROXY: TransportAdapter(
        name=TRANSPORT_NAIVEPROXY,
        plane=PLANE_BACKHAUL,
        implementation_role="transports/backhaul/naiveproxy",
        supported_topologies=(TOPOLOGY_CASCADE,),
        implemented=False,
    ),
    TRANSPORT_HYSTERIA2: TransportAdapter(
        name=TRANSPORT_HYSTERIA2,
        plane=PLANE_BACKHAUL,
        implementation_role="transports/backhaul/hysteria2",
        supported_topologies=(TOPOLOGY_CASCADE,),
        implemented=False,
    ),
    TRANSPORT_WIREGUARD: TransportAdapter(
        name=TRANSPORT_WIREGUARD,
        plane=PLANE_BACKHAUL,
        implementation_role="transports/backhaul/wireguard",
        supported_topologies=(TOPOLOGY_CASCADE,),
        implemented=False,
    ),
}

def canonical_transport(name: str) -> str:
    """Return the canonical transport name or reject an invalid value."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("transport name must be a non-empty string")
    return name.strip().lower()


def _select_adapter(name: str, registry: dict[str, TransportAdapter], plane: str):
    adapter = get_transport_adapter(name, plane, registry)
    if not adapter.implemented:
        raise ValueError(f"transport is not implemented: {plane}/{adapter.name}")
    return adapter


def get_transport_adapter(
    name: str,
    plane: str,
    registry: dict[str, TransportAdapter] | None = None,
) -> TransportAdapter:
    """Return adapter metadata without requiring the adapter to be deployed."""
    if registry is None:
        registry = ACCESS_ADAPTERS if plane == PLANE_ACCESS else BACKHAUL_ADAPTERS
    canonical = canonical_transport(name)
    adapter = registry.get(canonical)
    if adapter is None:
        raise ValueError(f"unsupported {plane} transport: {name}")
    return adapter


def adapter_lifecycle(adapter: TransportAdapter) -> dict[str, object]:
    """Return the stable lifecycle contract exposed by an adapter."""
    return {
        "operations": adapter.lifecycle_operations,
        "deploy_tasks": "deploy",
        "verify_tasks": "verify",
        "rollback_install_tasks": "rollback_install",
        "rollback_update_tasks": "rollback_update",
        "remove_tasks": "remove",
    }


def validate_access_transport(name: str) -> str:
    """Validate a standalone access selection and return its canonical name."""
    return _select_adapter(name, ACCESS_ADAPTERS, PLANE_ACCESS).name


def validate_transport_plan(
    topology: str,
    access_transport: str,
    backhaul_transport: str | None = None,
) -> dict:
    """Validate and expand the transport selection for a topology.

    A Cascade backhaul is one paired adapter with a client endpoint on
    ingress and a server endpoint on egress. Standalone deployments have no
    backhaul and use only their access endpoint.
    """
    if topology not in (TOPOLOGY_STANDALONE, TOPOLOGY_CASCADE):
        raise ValueError(f"unsupported topology: {topology}")

    access = _select_adapter(access_transport, ACCESS_ADAPTERS, PLANE_ACCESS)
    if topology == TOPOLOGY_STANDALONE:
        if backhaul_transport not in (None, ""):
            raise ValueError("standalone topology cannot define a backhaul")
        return {
            "topology": topology,
            "bindings": [
                {
                    "node_role": "standalone",
                    "plane": PLANE_ACCESS,
                    "transport": access.name,
                    "endpoint": "server",
                }
            ],
        }

    if backhaul_transport in (None, ""):
        raise ValueError("cascade topology requires a backhaul transport")
    backhaul = _select_adapter(backhaul_transport, BACKHAUL_ADAPTERS, PLANE_BACKHAUL)
    return {
        "topology": topology,
        "bindings": [
            {
                "node_role": "ingress",
                "plane": PLANE_ACCESS,
                "transport": access.name,
                "endpoint": "server",
            },
            {
                "node_role": "ingress",
                "plane": PLANE_BACKHAUL,
                "transport": backhaul.name,
                "endpoint": "client",
            },
            {
                "node_role": "egress",
                "plane": PLANE_BACKHAUL,
                "transport": backhaul.name,
                "endpoint": "server",
            },
        ],
    }


def validate_deployment_transports(deployment: dict) -> dict:
    """Validate the new ``transports`` block and return its expanded plan."""
    if not isinstance(deployment, dict):
        raise TypeError("deployment must be an object")
    topology = deployment.get("topology")
    selected = deployment.get("transports")
    if not isinstance(selected, dict):
        raise TypeError("deployment must contain a transports object")
    access = selected.get("access", {}).get("transport")
    backhaul = selected.get("backhaul", {}).get("transport")
    return validate_transport_plan(topology, access, backhaul)
