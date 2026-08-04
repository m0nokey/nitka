# Cascade Shadowrocket client example

This profile is for the two-node Cascade topology. It is not a standalone
node profile.

The supported client format is Shadowrocket on iOS and macOS.

The public profile is
[`shadowrocket.ru.example.conf`](shadowrocket.ru.example.conf) in this
directory.
It contains placeholders for node addresses and demonstrates the three client
actions used by the routing model:

- `DIRECT` — user's local exit;
- `PROXY` — ingress and, where selected by Xray, the egress exit;
- `REJECT` — local client-side blocking.

Nitka fills deployment-specific node addresses when generating a real profile.
Never commit generated profiles containing real infrastructure values.
