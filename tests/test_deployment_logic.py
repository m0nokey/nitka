import base64
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
        self.assertEqual(
            deployment["transports"],
            {
                "access": {"transport": "xray-reality"},
                "backhaul": {"transport": "ssh-tun"},
            },
        )
        self.assertEqual(deployment["roles"]["ingress"]["backend"], BACKEND_XRAY)
        self.assertEqual(
            deployment["roles"]["egress"]["transport"], TRANSPORT_EXISTING_XRAY
        )
        self.assertTrue(deployment["services"]["ssh_tun"]["enabled"])
        self.assertNotEqual(deployment["settings"]["ssh_tun"]["port"], 22)
        self.assertGreaterEqual(deployment["settings"]["ssh_tun"]["port"], 20000)
        self.assertLessEqual(deployment["settings"]["ssh_tun"]["port"], 60000)

    def test_cascade_replaces_invalid_tun_transport_automatically(self):
        state = {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_user": "deploy",
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
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 egress",
                },
            },
        }
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        state["deployments"]["cascade-1"]["settings"]["ssh_tun"]["port"] = 22

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")
        self.assertNotEqual(variables["cascade_ssh_tun_ssh_port"], 22)
        self.assertGreaterEqual(variables["cascade_ssh_tun_ssh_port"], 1025)
        self.assertLessEqual(variables["cascade_ssh_tun_ssh_port"], 65535)

        state["project_env_b64"] = base64.b64encode(
            b"ssh_tun_port=31847\n"
        ).decode()
        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")
        self.assertEqual(variables["cascade_ssh_tun_ssh_port"], 31847)

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
                    "management_user": "legacy-ingress-user",
                    "bootstrap_user": "legacy-bootstrap",
                    "management_authorized_key": "ssh-ed25519 legacy-ingress",
                    "xray": {
                        "vision_port": 443,
                        "xhttp_port": 23457,
                        "reality_private_key": "private-reality-key",
                        "reality_public_key": "public-reality-key",
                        "reality_short_id": "0123456789abcdef",
                        "server_name": "example.com",
                        "access_keys": [
                            {
                                "key_id": "key-one",
                                "vision_uuid": "11111111-1111-4111-8111-111111111111",
                                "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                            },
                            {
                                "key_id": "key-two",
                                "vision_uuid": "33333333-3333-4333-8333-333333333333",
                                "xhttp_uuid": "44444444-4444-4444-8444-444444444444",
                            },
                        ],
                    },
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management_user": "legacy-egress-user",
                    "bootstrap_user": "root",
                    "management_authorized_key": "ssh-ed25519 legacy-egress",
                },
            }
        }
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")

        self.assertEqual(variables["system_base_deploy_user"], "deploy")
        self.assertEqual(
            variables["cascade_ingress_deploy_authorized_key"],
            "ssh-ed25519 legacy-ingress",
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
            [key["key_id"] for key in variables["cascade_ingress_xray_access_keys"]],
            ["key-one", "key-two"],
        )
        self.assertEqual(
            variables["cascade_ssh_tun_public_host"],
            "192.0.2.20",
        )
        self.assertEqual(
            variables["cascade_ingress_harden_ssh_preserve_users"],
            ["legacy-ingress-user", "legacy-bootstrap"],
        )
        self.assertEqual(
            variables["cascade_ingress_xray_local_region_countries"], []
        )
        self.assertEqual(
            variables["cascade_egress_harden_ssh_preserve_users"],
            ["legacy-egress-user"],
        )
        self.assertEqual(
            [source["name"] for source in variables["cascade_egress_unbound_rpz_sources"]],
            ["urlhaus", "hagezi-tif-mini", "threatfox", "hagezi-dyndns"],
        )

    def test_cascade_maps_legacy_transport_port_and_separate_keys(self):
        state = {
            "ssh_tun_port": 31847,
            "ssh_tun_private_key_b64": "-----BEGIN OPENSSH PRIVATE KEY----- auth",
            "ssh_tun_public_key": "ssh-ed25519 AAAA auth",
            "ssh_tun_host_private_key_b64": "-----BEGIN OPENSSH PRIVATE KEY----- host",
            "ssh_tun_host_public_key": "ssh-ed25519 AAAA host",
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_user": "deploy",
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
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 egress",
                },
            },
        }
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")

        self.assertEqual(variables["cascade_ssh_tun_ssh_port"], 31847)
        self.assertEqual(variables["cascade_ssh_tun_container_port"], 22)
        self.assertEqual(
            variables["cascade_ssh_tun_auth_private_key"],
            "-----BEGIN OPENSSH PRIVATE KEY----- auth",
        )
        self.assertEqual(
            variables["cascade_ssh_tun_host_private_key"],
            "-----BEGIN OPENSSH PRIVATE KEY----- host",
        )
        self.assertEqual(
            variables["cascade_ssh_tun_host_public_key"],
            "ssh-ed25519 AAAA host",
        )


if __name__ == "__main__":
    unittest.main()
