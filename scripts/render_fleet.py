#!/usr/bin/env python3
"""Render the node fleet with Cascade deployments grouped together."""

import argparse
import concurrent.futures
import json
from datetime import datetime

try:
    from scripts.render_nodes import node_diagnostics
except ModuleNotFoundError:
    from render_nodes import node_diagnostics


def date_value(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return "N/A"


def status_for_node(node, diagnostics=None):
    if diagnostics and id(node) in diagnostics:
        if node.get("role") == "egress":
            return "Active" if diagnostics[id(node)]["management"].get("ssh") == "connected" else "Unreachable"
        return diagnostics[id(node)].get("status", "Unknown")
    return node.get("status", "Active")


def deployment_status(nodes, diagnostics=None):
    statuses = [status_for_node(node, diagnostics) for node in nodes]
    return "Active" if statuses and all(item == "Active" for item in statuses) else "Partial"


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
    print(
        f"{'':<5}{'IP':<15} {'STATUS':<8} {'COUNTRY':<9} "
        f"{'CREATED':<12} {'MODE':<17} PROVIDER"
    )
    print()
    for number, (kind, _, first, second) in enumerate(items, 1):
        first_mode = "Cascade ingress" if kind == "cascade" else first.get("mode", "Xray")
        first_values = (
            first.get("host", "N/A"),
            deployment_status((first, second), diagnostics) if kind == "cascade" else status_for_node(first, diagnostics),
            first.get("country", "N/A"),
            date_value(first.get("created_at")),
            first_mode,
            first.get("provider", "N/A"),
        )
        print(
            f"  {number}. {first_values[0]:<15} {first_values[1]:<8} "
            f"{first_values[2]:<9} {first_values[3]:<12} {first_values[4]:<17} {first_values[5]}"
        )
        if kind == "cascade":
            second_values = (
                second.get("host", "N/A"),
                status_for_node(second, diagnostics),
                second.get("country", "N/A"),
                date_value(second.get("created_at")),
                "Cascade egress",
                second.get("provider", "N/A"),
            )
            print(
                f"  └─ {second_values[0]:<15} {second_values[1]:<8} "
                f"{second_values[2]:<9} {second_values[3]:<12} {second_values[4]:<17} {second_values[5]}"
            )
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
