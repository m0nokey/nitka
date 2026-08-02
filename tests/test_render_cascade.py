import io
import unittest
from contextlib import redirect_stdout

from scripts.render_cascade import render
from scripts.render_fleet import fleet_items, render as render_fleet


def state_fixture():
    return {
        "nodes": {
            "ingress": {
                "host": "203.0.113.42",
                "country": "DE",
                "created_at": "2026-07-27T00:00:00+00:00",
                "provider": "Hetzner Online GmbH",
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
    def test_cascade_screen_keeps_roles_and_aligned_data(self):
        output = io.StringIO()
        with redirect_stdout(output):
            render(state_fixture(), "cascade-1")
        text = output.getvalue()
        self.assertIn("Status: Active", text)
        self.assertIn("Route: ingress → egress", text)
        self.assertIn("ingress   203.0.113.42", text)
        self.assertIn("egress    192.0.2.24", text)
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
        self.assertIn("└─ 192.0.2.24", text)
        self.assertIn("198.51.100.17", text)


if __name__ == "__main__":
    unittest.main()
