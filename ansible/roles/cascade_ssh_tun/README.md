# SSH TUN Transport

This role namespace is reserved for the replaceable egress transport layer.

Current SSH TUN implementation lives here:

- `templates/client`
- `templates/server`
- `tasks/client.yml`
- `tasks/server.yml`

Use this role with `ssh_tun_endpoint: client` on the ingress node and
`ssh_tun_endpoint: server` on the egress node.
