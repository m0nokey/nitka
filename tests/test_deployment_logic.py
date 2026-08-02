import copy
import unittest

from scripts.deployment_logic import (
    BACKEND_XRAY,
    TRANSPORT_EXISTING_XRAY,
    attach_cascade,
    cascade_ansible_vars,
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

    def test_cascade_ansible_vars_map_existing_xray_state(self):
        state = {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_authorized_key": "ssh-ed25519 ingress",
                    "xray": {
                        "vision_port": 443,
                        "xhttp_port": 23457,
                        "reality_private_key": "private-reality-key",
                        "reality_public_key": "public-reality-key",
                        "reality_short_id": "0123456789abcdef",
                        "server_name": "example.com",
                        "access_keys": [{
                            "vision_uuid": "11111111-1111-4111-8111-111111111111",
                            "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                        }],
                    },
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management_authorized_key": "ssh-ed25519 egress",
                },
            }
        }
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        variables = cascade_ansible_vars(state, "cascade-1", "/state/xray/cascade")

        self.assertEqual(variables["system_base_deploy_user"], "deploy")
        self.assertEqual(
            variables["cascade_ingress_deploy_authorized_key"],
            "ssh-ed25519 ingress",
        )
        self.assertEqual(
            variables["cascade_egress_remote_dir"],
            "/opt/nitka/cascade/egress",
        )
        self.assertEqual(
            variables["cascade_ingress_xray_reality_uuid"],
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(
            variables["cascade_ssh_tun_public_host"],
            "192.0.2.20",
        )


if __name__ == "__main__":
    unittest.main()
