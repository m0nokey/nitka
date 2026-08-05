# Standalone SSH access transport

Status: implemented as a fast temporary TCP proxy based on OpenSSH.

The adapter runs a dedicated OpenSSH container on the standalone VPS. It uses
public-key authentication and allows client-initiated TCP forwarding so a
Shadowrocket client can use the endpoint as a SOCKS proxy. Native UDP is not
supported by OpenSSH. If an optional external UDP relay is configured
separately, it uses UDP-over-TCP and may be unstable for calls, games, and
realtime audio. The standard deployment does not include a UDP relay. Each
client access key has its own generated username and Ed25519 key pair.
Usernames use lowercase letters and digits.

The container does not provide a shell, TUN device, agent forwarding, X11, or
remote gateway ports. Host keys persist in a dedicated Docker volume, while
the authorized client keys are supplied by the encrypted local state. Each
proxy user has no shell and is restricted to forwarding; management SSH uses
a separate user and key.

This adapter is independent from Hermes, Xray, and the Cascade SSH TUN
backhaul.
