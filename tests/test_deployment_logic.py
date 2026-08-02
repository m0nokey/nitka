import copy
import unittest

from scripts.deployment_logic import (
    BACKEND_XRAY,
    TRANSPORT_EXISTING_XRAY,
    attach_cascade,
    cascade_deployment,
    validate_deployments,
)


class DeploymentLogicTests(unittest.TestCase):
    @staticmethod
    def state():
        return {
            "nodes": {
                "ingress-node": {"host": "192.0.2.10", "xray": {"access_keys": []}},
                "egress-node": {"host": "192.0.2.20", "xray": {"access_keys": []}},
            }
        }

    def test_cascade_uses_current_xray_adapter_for_both_roles(self):
        deployment = cascade_deployment("cascade-1", "ingress-node", "egress-node")

        self.assertEqual(deployment["topology"], "cascade")
        self.assertEqual(deployment["roles"]["ingress"]["backend"], BACKEND_XRAY)
        self.assertEqual(
            deployment["roles"]["egress"]["transport"], TRANSPORT_EXISTING_XRAY
        )
        self.assertTrue(deployment["services"]["ssh_tun"]["enabled"])

    def test_attach_preserves_nodes_and_does_not_mutate_input(self):
        state = self.state()
        original = copy.deepcopy(state)

        result = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        self.assertEqual(state, original)
        self.assertEqual(set(result["nodes"]), set(state["nodes"]))
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(
            result["deployments"]["cascade-1"]["roles"]["ingress"]["node"],
            "ingress-node",
        )
        self.assertTrue(validate_deployments(result))

    def test_cascade_requires_two_existing_nodes(self):
        with self.assertRaisesRegex(ValueError, "egress node not found"):
            attach_cascade(self.state(), "cascade-1", "ingress-node", "missing")

        with self.assertRaisesRegex(ValueError, "different nodes"):
            attach_cascade(self.state(), "cascade-1", "ingress-node", "ingress-node")

    def test_validate_rejects_unknown_role_node(self):
        state = self.state()
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        state["deployments"]["cascade-1"]["roles"]["egress"]["node"] = "missing"

        with self.assertRaisesRegex(ValueError, "deployment node not found"):
            validate_deployments(state)


if __name__ == "__main__":
    unittest.main()
