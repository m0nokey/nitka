import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.render_cascade import cascade_node_diagnostics, render
from scripts.render_fleet import fleet_items, render as render_fleet


def state_fixture():
    return {
        "nodes": {
            "ingress": {
                "host": "203.0.113.42",
                "country": "DE",
                "created_at": "2026-07-27T00:00:00+00:00",
                "provider": "Example Cloud Provider",
                "status": "Active",
            },
            "egress": {
                "host": "192.0.2.24",
                "country": "NL",
                "created_at": "2026-07-25T00:00:00+00:00",
                "provider": "DigitalOcean, LLC",
                "status": "Active",
            },
            "single": {"host": "198.51.100.17", "status": "Partial"},
        },
        "deployments": {
            "cascade-1": {
                "roles": {
                    "ingress": {"node": "ingress"},
                    "egress": {"node": "egress"},
                }
            }
        },
    }


class CascadeRenderTests(unittest.TestCase):
    def test_cascade_diagnostics_pass_role_to_service_probe(self):
        with patch("scripts.render_cascade.node_diagnostics", return_value={}) as probe:
            cascade_node_diagnostics("egress", {"host": "192.0.2.24"})
        probe.assert_called_once_with({"host": "192.0.2.24", "topology": {"role": "egress"}})

    def test_cascade_screen_keeps_roles_and_aligned_data(self):
        output = io.StringIO()
        with redirect_stdout(output):
            render(state_fixture(), "cascade-1")
        text = output.getvalue()
        self.assertIn("Status: Active", text)
        self.assertIn("Route: ingress → egress", text)
        lines = text.splitlines()
        header = next(line for line in lines if "ROLE" in line and "PROVIDER" in line)
        role_column = header.index("ROLE")
        ip_column = header.index("IP")
        ingress = next(line for line in lines if line.lstrip().startswith("┌─"))
        egress = next(line for line in lines if line.lstrip().startswith("└─"))
        self.assertEqual(ingress.index("ingress"), role_column)
        self.assertEqual(egress.index("egress"), role_column)
        self.assertEqual(ingress.index("203.0.113.42"), ip_column)
        self.assertEqual(egress.index("192.0.2.24"), ip_column)
        self.assertIn("SSH TUN + DNS", text)

    def test_fleet_groups_cascade_nodes_and_keeps_standalone_nodes(self):
        items = fleet_items(state_fixture())
        self.assertEqual([(item[0], item[1]) for item in items], [
            ("cascade", "cascade-1"),
            ("node", "single"),
        ])
        output = io.StringIO()
        with redirect_stdout(output):
            render_fleet(state_fixture())
        text = output.getvalue()
        self.assertIn("Node Management:", text)
        self.assertNotIn("Fleet status:", text)
        self.assertIn("1. 203.0.113.42", text)
        self.assertIn("└─ 192.0.2.24", text)
        self.assertNotIn("2. 192.0.2.24", text)
        self.assertIn("198.51.100.17", text)
        lines = text.splitlines()
        header = next(line for line in lines if "IP" in line and "PROVIDER" in line)
        ingress = next(line for line in lines if "1. 203.0.113.42" in line)
        egress = next(line for line in lines if "└─ 192.0.2.24" in line)
        self.assertTrue(ingress.startswith("  1. "))
        self.assertTrue(egress.startswith("  └─ "))
        header_positions = [header.index(field) for field in (
            "IP", "STATUS", "COUNTRY", "CREATED", "MODE", "PROVIDER"
        )]
        for row, values in (
            (ingress, ("203.0.113.42", "Active", "DE", "2026-07-27", "Cascade ingress", "Example Cloud Provider")),
            (egress, ("192.0.2.24", "Active", "NL", "2026-07-25", "Cascade egress", "DigitalOcean, LLC")),
        ):
            value_positions = [row.index(value) for value in values]
            self.assertEqual(header_positions, value_positions)

    def test_fleet_table_expands_every_column_after_long_status(self):
        state = state_fixture()
        state["nodes"]["single"]["status"] = "VPN unavailable"
        output = io.StringIO()
        with redirect_stdout(output):
            render_fleet(state)
        lines = output.getvalue().splitlines()
        header = next(line for line in lines if "IP" in line and "PROVIDER" in line)
        row = next(line for line in lines if "198.51.100.17" in line)
        fields = ("IP", "STATUS", "COUNTRY", "CREATED", "MODE", "PROVIDER")
        header_positions = [header.index(field) for field in fields]
        row_positions = []
        start = 0
        for value in (
            "198.51.100.17",
            "VPN unavailable",
            "N/A",
            "N/A",
            "Xray",
            "N/A",
        ):
            position = row.index(value, start)
            row_positions.append(position)
            start = position + len(value)
        self.assertEqual(header_positions, row_positions)


if __name__ == "__main__":
    unittest.main()
