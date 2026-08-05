#!/usr/bin/env python3
"""Render the node fleet with Cascade deployments grouped together."""

import argparse
import concurrent.futures
import json
from datetime import datetime

try:
    from scripts.render_nodes import node_diagnostics
    from scripts.table import column_widths, format_row
except ModuleNotFoundError:
    from render_nodes import node_diagnostics
    from table import column_widths, format_row


def date_value(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return "N/A"


def status_for_node(node, diagnostics=None):
    if diagnostics and id(node) in diagnostics:
        if node.get("topology", {}).get("role") == "egress":
            return "Active" if diagnostics[id(node)]["management"].get("ssh") == "connected" else "Unreachable"
        return diagnostics[id(node)].get("status", "Unknown")
    return node.get("status", "Active")


def deployment_status(nodes, diagnostics=None):
    statuses = [status_for_node(node, diagnostics) for node in nodes]
    return "Active" if statuses and all(item == "Active" for item in statuses) else "Partial"


def node_mode(node):
    if node.get("access", {}).get("transport") == "ssh-proxy":
        return "SSH proxy"
    return node.get("mode", "Xray")


def fleet_items(state):
    nodes = state.get("nodes", {})
    deployments = state.get("deployments", {})
    used = set()
    items = []
    for deployment_id, deployment in deployments.items():
        roles = deployment.get("roles", {})
        ingress_name = roles.get("ingress", {}).get("node")
        egress_name = roles.get("egress", {}).get("node")
        ingress = nodes.get(ingress_name)
        egress = nodes.get(egress_name)
        if not isinstance(ingress, dict) or not isinstance(egress, dict):
            continue
        used.update((ingress_name, egress_name))
        items.append(("cascade", deployment_id, ingress, egress))
    for name, node in nodes.items():
        if name not in used:
            items.append(("node", name, node, None))
    return items


def render(state, diagnostics=None):
    items = fleet_items(state)

    print("Node Management:")
    print()
    table_rows = []
    for number, (kind, _, first, second) in enumerate(items, 1):
        first_mode = "Cascade ingress" if kind == "cascade" else node_mode(first)
        table_rows.append((
            f"{number}.",
            first.get("host", "N/A"),
            deployment_status((first, second), diagnostics) if kind == "cascade" else status_for_node(first, diagnostics),
            first.get("country", "N/A"),
            date_value(first.get("created_at")),
            first_mode,
            first.get("provider", "N/A"),
        ))
        if kind == "cascade":
            table_rows.append((
                "└─",
                second.get("host", "N/A"),
                status_for_node(second, diagnostics),
                second.get("country", "N/A"),
                date_value(second.get("created_at")),
                "Cascade egress",
                second.get("provider", "N/A"),
            ))
    headers = ("IP", "STATUS", "COUNTRY", "CREATED", "MODE", "PROVIDER")
    data_rows = [row[1:] for row in table_rows]
    widths = column_widths(headers, data_rows)
    print(format_row(headers, widths, indent="   ", gap="   "))
    for row in table_rows:
        print(f"{row[0]} {format_row(row[1:], widths, gap='   ')}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", action="store_true")
    parser.add_argument("--check", action="store_true")
    state = json.load(__import__("sys").stdin)
    items = fleet_items(state)
    args = parser.parse_args()
    if args.items:
        for kind, name, *_ in items:
            print(f"{kind}\t{name}")
        return
    diagnostics = None
    if args.check:
        nodes = {id(node): node for _, _, first, second in items for node in (first, second) if node}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(nodes)))) as pool:
            futures = {pool.submit(node_diagnostics, node): node for node in nodes.values()}
            diagnostics = {id(node): future.result() for future, node in futures.items()}
    render(state, diagnostics)


if __name__ == "__main__":
    main()
