#!/usr/bin/env python3
import argparse
import json
import sys
from urllib.parse import quote

from access_naming import (
    LINK_TOPOLOGY_CASCADE,
    LINK_TOPOLOGY_CASCADE_REVERSE,
    link_name,
    normalize_country,
)

RESET = "\033[0m"
BLUE = "\033[38;5;117m"
USE_COLOR = sys.stdout.isatty()


def color(text, value):
    return f"{value}{text}{RESET}" if USE_COLOR else text


parser = argparse.ArgumentParser()
parser.add_argument("node")
args = parser.parse_args()
state = json.load(sys.stdin)
node = state.get("nodes", {}).get(args.node)
if node is None:
    raise SystemExit("node not found")


def node_topology(state, node_name):
    matches = []
    for deployment in state.get("deployments", {}).values():
        if not isinstance(deployment, dict):
            continue
        roles = deployment.get("roles", {})
        ingress = roles.get("ingress", {}) if isinstance(roles, dict) else {}
        if isinstance(ingress, dict) and ingress.get("node") == node_name:
            matches.append(
                deployment.get("topology_variant", LINK_TOPOLOGY_CASCADE)
            )
    if len(matches) == 1 and matches[0] in {
        LINK_TOPOLOGY_CASCADE,
        LINK_TOPOLOGY_CASCADE_REVERSE,
    }:
        return matches[0]
    return ""


xray = node.get("access", {}).get("xray_reality", {})
keys = xray.get("access_keys", [])
server_name = xray.get("server_name", "github.com")
country = normalize_country(node.get("country"))
topology = node_topology(state, args.node)
if not keys:
    print()
    print(color("Manage access keys:", BLUE))
    print("\n  No access keys configured.\n")
    print(f"  {color('1.', BLUE)} Add key")
    raise SystemExit(0)

for index, key in enumerate(keys, 1):
    print(color(f"{index}.", BLUE))
    print(
        f"vless://{key['vision_uuid']}@{node['host']}:{xray['vision_port']}"
        f"?type=tcp&encryption=none&flow=xtls-rprx-vision&security=reality"
        f"&sni={server_name}&fp=chrome&headerType=none"
        f"&pbk={xray['reality_public_key']}&sid={xray['reality_short_id']}"
        f"#{link_name(country, key['share_id'], 'vless-reality-vision', topology)}"
    )
    print()
    print(
        f"vless://{key['xhttp_uuid']}@{node['host']}:{xray['xhttp_port']}"
        f"?type=xhttp&encryption=none&security=reality"
        f"&sni={server_name}&fp=chrome"
        f"&pbk={xray['reality_public_key']}&sid={xray['reality_short_id']}"
        f"&path={quote(xray.get('xhttp_path', '/'), safe='')}&mode=packet-up"
        f"#{link_name(country, key['share_id'], 'vless-reality-xhttp', topology)}"
    )
    if index != len(keys):
        print()
        print()
        print()
    else:
        print()
        print()
