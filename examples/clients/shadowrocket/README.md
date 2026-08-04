# Shadowrocket client example

The supported client format is Shadowrocket on iOS and macOS.

The public profile is `shadowrocket.ru.example.conf` at the repository root.
It contains placeholders for node addresses and demonstrates the three client
actions used by the routing model:

- `DIRECT` — user's local exit;
- `PROXY` — ingress and, where selected by Xray, the egress exit;
- `REJECT` — local client-side blocking.

Nitka fills deployment-specific node addresses when generating a real profile.
Never commit generated profiles containing real infrastructure values.
