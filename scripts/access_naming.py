"""Stable names for user-facing access keys."""

from __future__ import annotations

import hashlib
import re
import secrets
import string

SHARE_ID_PATTERN = re.compile(r"^k[a-z0-9]{6}$")
_SHARE_ID_ALPHABET = string.ascii_lowercase + string.digits
LINK_TOPOLOGY_CASCADE = "cascade"
LINK_TOPOLOGY_CASCADE_REVERSE = "cascade-reverse"
_LINK_TOPOLOGIES = {
    LINK_TOPOLOGY_CASCADE,
    LINK_TOPOLOGY_CASCADE_REVERSE,
}


def normalize_country(value: object) -> str:
    """Return a safe two-letter country label for generated link names."""
    country = str(value or "").strip().lower()
    return country if re.fullmatch(r"[a-z]{2}", country) else "xx"


def new_share_id(existing: set[str]) -> str:
    """Generate one short, lower-case identifier that is unique in a key set."""
    while True:
        share_id = "k" + "".join(
            secrets.choice(_SHARE_ID_ALPHABET) for _ in range(6)
        )
        if share_id not in existing:
            return share_id


def legacy_share_id(key: dict, existing: set[str]) -> str:
    """Derive a stable identifier for a key created before share_id existed."""
    source = next(
        (
            str(key.get(field))
            for field in ("key_id", "vision_uuid", "xhttp_uuid")
            if key.get(field)
        ),
        "legacy-key",
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    for offset in range(0, len(digest) - 6):
        share_id = "k" + digest[offset : offset + 6]
        if share_id not in existing:
            return share_id
    raise ValueError("could not derive a unique access key share_id")


def link_name(
    country: object,
    share_id: str,
    transport: str,
    topology: str = "",
) -> str:
    """Build a stable fragment, adding topology only for cascade links."""
    if topology and topology not in _LINK_TOPOLOGIES:
        raise ValueError(f"unsupported link topology: {topology}")
    parts = ["nitka", normalize_country(country), share_id]
    if topology:
        parts.append(topology)
    parts.append(transport)
    return "-".join(parts)
