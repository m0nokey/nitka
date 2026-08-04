from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TransportDispatchTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_standalone_playbook_uses_access_dispatcher(self):
        content = self.read("ansible/site.yml")

        self.assertIn("- role: transports/access", content)
        self.assertNotIn("- role: xray", content)

    def test_cascade_playbooks_use_plane_dispatchers(self):
        ingress = self.read("ansible/cascade_ingress.yml")
        egress = self.read("ansible/cascade_egress.yml")

        self.assertIn("- role: transports/access", ingress)
        self.assertIn("- role: transports/backhaul", ingress)
        self.assertIn("- role: transports/backhaul", egress)
        self.assertNotIn("- role: cascade_ssh_tun", ingress)
        self.assertNotIn("- role: cascade_ssh_tun", egress)

    def test_dispatchers_bind_only_implemented_adapters(self):
        access = self.read("ansible/roles/transports/access/tasks/main.yml")
        backhaul = self.read("ansible/roles/transports/backhaul/tasks/main.yml")

        self.assertIn("name: transports/access/xray", access)
        self.assertIn("name: transports/backhaul/ssh_tun", backhaul)
        self.assertIn("xray-reality", access)
        self.assertIn("ssh-tun", backhaul)


if __name__ == "__main__":
    unittest.main()
