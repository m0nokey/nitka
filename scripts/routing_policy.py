"""Import and normalize Nitka Xray routing variables."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROUTING_FIELDS = (
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
)

CANONICAL_ROUTING_PREFIX = "access_xray_"
CANONICAL_CLASH_CONCURRENCY_KEY = "topology_cascade_ingress_clash_tcp_concurrent"


def _list_value(value, key, *, allow_objects=False):
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"routing variable must be a list: {key}")
    if any(
        not isinstance(item, str)
        and not (allow_objects and isinstance(item, dict))
        for item in value
    ):
        raise TypeError(f"routing variable contains an invalid value: {key}")
    return list(value)


def _unique(values):
    result = []
    seen = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _aggregate(raw, aggregate_keys, group_prefixes):
    """Resolve a Jinja aggregate while retaining its declared order."""
    aggregate_keys = (
        (aggregate_keys,) if isinstance(aggregate_keys, str) else tuple(aggregate_keys)
    )
    prefixes = (
        (group_prefixes,)
        if isinstance(group_prefixes, str)
        else tuple(group_prefixes)
    )
    for aggregate_key in aggregate_keys:
        value = raw.get(aggregate_key)
        if isinstance(value, list):
            return _unique(_list_value(value, aggregate_key))

    names = []
    for aggregate_key in aggregate_keys:
        value = raw.get(aggregate_key)
        if isinstance(value, str):
            names = [
                name
                for name in re.findall(
                    r"access_xray_[A-Za-z0-9_]+", value
                )
                if any(name.startswith(prefix) for prefix in prefixes)
            ]
            if names:
                break
    if not names:
        names = [
            key for key in raw
            if any(key.startswith(prefix) for prefix in prefixes)
            and isinstance(raw[key], list)
        ]

    result = []
    for name in names:
        result.extend(_list_value(raw.get(name), name))
    return _unique(result)


def _routing_value(raw, field, *, allow_objects=False):
    """Read one canonical access_xray routing field."""
    canonical_key = f"{CANONICAL_ROUTING_PREFIX}{field}"
    return _list_value(raw.get(canonical_key), canonical_key, allow_objects=allow_objects)


def _load_yaml(path):
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - controller dependency
        raise RuntimeError("PyYAML is required to import routing rules") from exc

    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read routing file: {source}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"routing file is invalid YAML: {source}") from exc
    if not isinstance(raw, dict):
        raise TypeError("routing file must contain a YAML object")
    return source, raw


def import_routing_policy(state, node_name, path):
    """Return a state copy with routing policy attached to one Xray node."""
    if not isinstance(state, dict) or not isinstance(state.get("nodes"), dict):
        raise TypeError("state must contain a nodes object")
    if node_name not in state["nodes"]:
        raise ValueError(f"node not found: {node_name}")

    state = deepcopy(state)
    source, raw = _load_yaml(path)
    node = state["nodes"][node_name]
    if not isinstance(node, dict):
        raise TypeError(f"node must be an object: {node_name}")
    xray = node.setdefault("access", {}).setdefault("xray_reality", {})
    if not isinstance(xray, dict):
        raise TypeError(f"node xray state must be an object: {node_name}")

    effective = {
        "block_domains": _routing_value(raw, "block_domains"),
        "block_ips": _routing_value(raw, "block_ips"),
        "reality_direct_domains": _routing_value(raw, "reality_direct_domains"),
        "reality_direct_ips": _routing_value(raw, "reality_direct_ips"),
        "xhttp_direct_domains": _routing_value(raw, "xhttp_direct_domains"),
        "xhttp_direct_ips": _routing_value(raw, "xhttp_direct_ips"),
        "reality_proxy_domains": _aggregate(
            raw,
            "access_xray_reality_proxy_domains",
            "access_xray_reality_proxy_domains_",
        ),
        "reality_proxy_ips": _aggregate(
            raw,
            "access_xray_reality_proxy_ips",
            "access_xray_reality_proxy_ips_",
        ),
        "dns_direct_domains": _aggregate(
            raw,
            "access_xray_dns_direct_domains",
            ("access_xray_reality_direct_domains", "access_xray_xhttp_direct_domains"),
        ),
        "dns_direct_servers": _routing_value(
            raw, "dns_direct_servers", allow_objects=True
        ),
    }
    effective = {key: _unique(value) for key, value in effective.items()}

    categories = {}
    for key, value in raw.items():
        if not isinstance(value, list):
            continue
        if key.startswith("access_xray_reality_proxy_domains_"):
            categories.setdefault("reality_proxy_domains", {})[
                key.removeprefix("access_xray_reality_proxy_domains_")
            ] = _list_value(value, key)
        elif key.startswith("access_xray_reality_proxy_ips_"):
            categories.setdefault("reality_proxy_ips", {})[
                key.removeprefix("access_xray_reality_proxy_ips_")
            ] = _list_value(value, key)

    xray.update(effective)
    xray["clash_tcp_concurrent"] = bool(
        raw.get(
            CANONICAL_CLASH_CONCURRENCY_KEY,
            True,
        )
    )
    xray["routing_policy"] = {
        "format": "nitka-routing-v1",
        "source_name": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": categories,
        # Keep the complete policy in the encrypted Vault. The effective
        # lists below are used by Ansible, while this snapshot makes the
        # import reproducible without putting the user's domain lists in Git.
        "source_variables": deepcopy(raw),
        "effective": effective,
    }
    return state
