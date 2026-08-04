#!/usr/bin/env python3
"""Render the compact management screen for one Cascade deployment."""

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


def node_status(node, diagnostics=None):
    if diagnostics and id(node) in diagnostics:
        return diagnostics[id(node)].get("status", "Unknown")
    return node.get("status", "Active")


def cascade_node_diagnostics(role, node):
    probe_node = dict(node)
    probe_node["role"] = role
    return node_diagnostics(probe_node)


def deployment_data(state, deployment_id):
    deployment = state.get("deployments", {}).get(deployment_id)
    if not isinstance(deployment, dict):
        raise TypeError(f"deployment not found: {deployment_id}")
    nodes = state.get("nodes", {})
    roles = deployment.get("roles", {})
    result = []
    for role in ("ingress", "egress"):
        node_name = roles.get(role, {}).get("node")
        node = nodes.get(node_name)
        if not isinstance(node, dict):
            raise TypeError(f"deployment node not found: {deployment_id}/{role}")
        result.append((role, node))
    return result


def format_table(headers, rows, indent="  "):
    values = [tuple(str(value) for value in headers)]
    values.extend(tuple(str(value) for value in row) for row in rows)
    widths = [max(len(row[index]) for row in values) for index in range(len(headers))]
    return [
        indent + "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in values
    ]


def render(state, deployment_id, diagnostics=None):
    rows = deployment_data(state, deployment_id)
    statuses = [node_status(node, diagnostics) for _, node in rows]
    deployment_status = "Active" if all(status == "Active" for status in statuses) else "Partial"
    connectivity = "healthy" if deployment_status == "Active" else "degraded"

    print("Cascaded VPN")
    print()
    print(
        f"Status: {deployment_status:<8} Route: ingress → egress    "
        f"Connectivity: {connectivity}"
    )
    print()
    table_rows = []
    for index, (role, node) in enumerate(rows):
        mode = node.get("mode") or ("Xray" if role == "ingress" else "SSH TUN + DNS")
        table_rows.append((
            "┌─" if index == 0 else "└─",
            role,
            node.get("host", "N/A"),
            node_status(node, diagnostics),
            node.get("country", "N/A"),
            date_value(node.get("created_at")),
            mode,
            node.get("provider", "N/A"),
        ))
    for line in format_table(
        ("", "ROLE", "IP", "STATUS", "COUNTRY", "CREATED", "MODE", "PROVIDER"),
        table_rows,
    ):
        print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("deployment")
    parser.add_argument("--check", action="store_true")
    state = json.load(__import__("sys").stdin)
    args = parser.parse_args()
    try:
        diagnostics = None
        if args.check:
            rows = deployment_data(state, args.deployment)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(cascade_node_diagnostics, role, node): node
                    for role, node in rows
                }
                diagnostics = {id(node): future.result() for future, node in futures.items()}
        render(state, args.deployment, diagnostics)
    except (TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
