from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ModularArchitectureTests(unittest.TestCase):
    def test_canonical_playbook_surface_exists(self):
        expected = {
            "preflight.yml",
            "bootstrap.yml",
            "harden_ssh.yml",
            "finalize_ssh.yml",
            "deploy_standalone.yml",
            "deploy_cascade.yml",
            "manage_access.yml",
            "remove.yml",
        }
        actual = {path.name for path in (ROOT / "ansible/playbooks").glob("*.yml")}
        self.assertTrue(expected <= actual)
        self.assertFalse((ROOT / "ansible/site.yml").exists())

    def test_transport_boundaries_are_independent(self):
        access_dispatcher = (ROOT / "ansible/roles/transports/access/tasks/main.yml").read_text()
        backhaul_dispatcher = (ROOT / "ansible/roles/transports/backhaul/tasks/main.yml").read_text()
        registry = (ROOT / "scripts/transport_registry.py").read_text()

        self.assertIn('access_selected_transport | replace(\'-\', \'_\')', access_dispatcher)
        self.assertIn('backhaul_transport_selection | default(\'ssh-tun\') | replace(\'-\', \'_\')', backhaul_dispatcher)
        self.assertIn('implementation_role="transports/access/xray_reality"', registry)
        self.assertIn('implementation_role="transports/backhaul/ssh_tun"', registry)
        self.assertNotIn("cascade_ssh_tun", access_dispatcher + backhaul_dispatcher)

    def test_implemented_adapters_expose_the_full_lifecycle(self):
        lifecycle_files = (
            "main.yml",
            "deploy.yml",
            "verify.yml",
            "restart.yml",
            "rollback_install.yml",
            "rollback_update.yml",
            "remove.yml",
        )
        for role in (
            ROOT / "ansible/roles/transports/access/xray_reality",
            ROOT / "ansible/roles/transports/access/ssh_proxy",
            ROOT / "ansible/roles/transports/backhaul/ssh_tun",
        ):
            for filename in lifecycle_files:
                self.assertTrue((role / "tasks" / filename).exists(), f"missing {role}/{filename}")

    def test_removal_is_dispatched_to_adapters(self):
        remove_playbook = (ROOT / "ansible/playbooks/remove.yml").read_text()
        self.assertIn("access_transport_operation: remove", remove_playbook)
        self.assertIn("backhaul_transport_operation: remove", remove_playbook)
        self.assertNotIn("/opt/xray", remove_playbook)

    def test_canonical_variable_names_are_used_by_adapters(self):
        role_files = list((ROOT / "ansible/roles/transports").rglob("*.yml"))
        role_files += list((ROOT / "ansible/roles/transports").rglob("*.j2"))
        text = "\n".join(path.read_text(encoding="utf-8") for path in role_files)

        self.assertIn("access_xray_state", text)
        self.assertIn("access_ssh_proxy_access_keys", text)
        self.assertIn("backhaul_ssh_tun_endpoint", text)
        self.assertNotIn("transports_ssh_", text)
        self.assertNotIn("transport_backhaul_", text)
        self.assertNotIn("xray_state", text.replace("access_xray_state", ""))

    def test_runtime_directories_follow_the_canonical_layout(self):
        xray_defaults = (ROOT / "ansible/roles/transports/access/xray_reality/defaults/main.yml").read_text()
        ssh_defaults = (ROOT / "ansible/roles/transports/access/ssh_proxy/defaults/main.yml").read_text()
        ingress_vars = (ROOT / "ansible/roles/topology/cascade/ingress/vars/main.yml").read_text()
        egress_vars = (ROOT / "ansible/roles/topology/cascade/egress/vars/main.yml").read_text()

        self.assertIn("/opt/nitka/standalone/access/xray-reality", xray_defaults)
        self.assertIn("/opt/nitka/standalone/access/ssh-proxy", ssh_defaults)
        self.assertIn("topology_cascade_ingress_access_dir_name: access/xray-reality", ingress_vars)
        self.assertIn("topology_cascade_ingress_backhaul_dir_name: backhaul/ssh-tun", ingress_vars)
        self.assertIn("topology_cascade_ingress_routing_dir_name: routing/clashrs", ingress_vars)
        self.assertIn("topology_cascade_egress_backhaul_dir_name: backhaul/ssh-tun", egress_vars)

    def test_old_runtime_variable_names_are_not_used_in_production_code(self):
        files = list((ROOT / "ansible").rglob("*.yml")) + list((ROOT / "ansible").rglob("*.j2"))
        files += list((ROOT / "scripts").glob("*.py"))
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("topology_cascade_ingress_xray_", text)
        self.assertNotIn("topology_cascade_backhaul_", text)
        self.assertNotIn("topology_cascade_ingress_ssh_", text)
        self.assertNotIn('"cascade_transport_bindings"', text)
        self.assertNotIn("vars_ =", text)

    def test_cascade_composition_does_not_select_a_transport_directly(self):
        topology = (ROOT / "ansible/playbooks/deploy_cascade.yml").read_text()
        self.assertIn("deploy_cascade_egress.yml", topology)
        self.assertIn("deploy_cascade_ingress.yml", topology)
        self.assertNotIn("cascade_ssh_tun", topology)


if __name__ == "__main__":
    unittest.main()
