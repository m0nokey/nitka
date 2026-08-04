import secrets

PORT_MODE_RANDOM = "random"
PORT_MODE_VISION_443 = "vision-443"
PORT_MODE_XHTTP_443 = "xhttp-443"
PORT_MODE_MANUAL = "manual"
VISION_PORT = 443


def _validated_port(value, label):
    if value is None or value == "":
        return None
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{label} must be between 1 and 65535")
    return port


def _port_entry(external, internal, source, internal_source=None):
    entry = {
        "external": _validated_port(external, "external port"),
        "internal": _validated_port(internal, "internal port"),
        "source": source,
    }
    entry["external_status"] = "known" if entry["external"] is not None else "not_recorded"
    if entry["internal"] is None:
        entry["internal_status"] = "not_recorded"
    elif internal_source == "inferred":
        entry["internal_status"] = "inferred"
    else:
        entry["internal_status"] = "known"
    if internal_source:
        entry["internal_source"] = internal_source
    return entry


def build_port_mapping(
    bootstrap_external=None,
    management_external=None,
    sshd_internal=None,
    services=None,
    *,
    bootstrap_internal=None,
    source="state",
    internal_source="recorded",
):
    """Build the common external-to-internal port model for every node.

    NAT here means detected port translation, not the mere existence of a
    private provider network. Missing values stay unknown instead of guessed.
    """
    bootstrap_external = _validated_port(bootstrap_external, "bootstrap external port")
    management_external = _validated_port(
        management_external, "management external port"
    )
    sshd_internal = _validated_port(sshd_internal, "sshd internal port")
    if management_external is None:
        management_external = bootstrap_external
    if bootstrap_internal is None and sshd_internal is not None:
        bootstrap_internal = sshd_internal

    if management_external is None or sshd_internal is None:
        nat = {
            "enabled": None,
            "detection": "unknown",
            "confidence": "none",
            "reason": "external management port or internal sshd port is not recorded",
        }
    elif management_external != sshd_internal:
        nat = {
            "enabled": True,
            "detection": "derived",
            "confidence": "high",
            "reason": (
                f"management external port {management_external} differs from "
                f"sshd internal port {sshd_internal}"
            ),
        }
    else:
        nat = {
            "enabled": False,
            "detection": "derived",
            "confidence": "high",
            "reason": "no SSH port translation detected",
        }

    ports = {
        "bootstrap_ssh": _port_entry(
            bootstrap_external,
            bootstrap_internal,
            source,
            internal_source if bootstrap_internal is not None else None,
        ),
        "management_ssh": _port_entry(
            management_external,
            sshd_internal,
            source,
            internal_source if sshd_internal is not None else None,
        ),
    }
    for service, internal in (services or {}).items():
        ports[service] = _port_entry(
            None,
            internal,
            source,
            "recorded" if internal is not None else None,
        )
    return {"nat": nat, "ports": ports}


def bot_port_pattern(port):
    value = str(port)
    if any(value[index] == value[index + 1] for index in range(len(value) - 1)):
        return True
    if any(sequence in value for sequence in ("01234", "12345", "23456", "34567", "45678", "56789")):
        return True
    if len(value) >= 5 and any(
        value[index] == value[index + 2] == value[index + 4]
        for index in range(len(value) - 4)
    ):
        return True
    if len(value) == 5:
        if value[0] == value[4] and value[1] == value[3]:
            return True
        if value[0] == value[3] and value[1] == value[4]:
            return True
    return len(value) >= 4 and any(
        value[index] == value[index + 2]
        and value[index + 1] == value[index + 3]
        for index in range(len(value) - 3)
    )


def generated_port(used):
    while True:
        port = secrets.randbelow(40001) + 20000
        value = str(port)
        if port in used or bot_port_pattern(port):
            continue
        if any(
            abs(int(value[index]) - int(value[index + 1])) < 2
            for index in range(len(value) - 1)
        ):
            continue
        used.add(port)
        return port


def generated_vpn_ports(used, mode=PORT_MODE_RANDOM, manual_ports=None):
    if mode == PORT_MODE_RANDOM:
        return generated_port(used), generated_port(used)
    if mode == PORT_MODE_VISION_443:
        return VISION_PORT, generated_port(used)
    if mode == PORT_MODE_XHTTP_443:
        return generated_port(used), VISION_PORT
    if mode == PORT_MODE_MANUAL:
        if manual_ports is None or len(manual_ports) != 2:
            raise ValueError("manual mode requires Vision and XHTTP ports")
        vision_port, xhttp_port = manual_ports
        ports = (vision_port, xhttp_port)
        if any(not isinstance(port, int) or not 1 <= port <= 65535 for port in ports):
            raise ValueError("manual VPN ports must be between 1 and 65535")
        if vision_port == xhttp_port:
            raise ValueError("manual VPN ports must be different")
        if any(port in used for port in ports):
            raise ValueError("manual VPN ports must not overlap existing ports")
        used.update(ports)
        return ports
    raise ValueError(f"unsupported VPN port mode: {mode}")
