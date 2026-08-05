import unittest

from scripts.render_nodes import (
    service_probe_command,
)


class NodeDiagnosticsTests(unittest.TestCase):
    def test_topology_cascade_ingress_probe_uses_cascade_container_name(self):
        command = service_probe_command({"topology": {"role": "ingress"}})

        self.assertIn("cascade-xray", command)
        self.assertIn("nitka-xray", command)
        self.assertIn("for entry in $running_containers", command)
        self.assertIn("docker ps -a", command)
        self.assertIn("service-running", command)

    def test_topology_cascade_egress_probe_uses_ssh_tun_container_name(self):
        command = service_probe_command({"topology": {"role": "egress"}})

        self.assertIn("cascade-ssh-tun-server", command)
        self.assertIn("nitka-ssh-tun-server", command)

    def test_standalone_ssh_probe_uses_ssh_transport_container(self):
        command = service_probe_command({"access": {"transport": "ssh-proxy"}})

        self.assertIn("nitka-ssh-transport", command)
        self.assertNotIn("cascade-xray", command)


if __name__ == "__main__":
    unittest.main()
