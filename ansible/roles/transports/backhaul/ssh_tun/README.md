# SSH TUN backhaul

Status: implemented.

This is the current Cascade backhaul between ingress and egress. Its concrete
runtime remains in `ansible/roles/transports/backhaul/ssh_tun`; this directory documents
the adapter boundary and prevents future backhauls from being coupled to the
Cascade orchestration code.

The adapter must provide:

- an ingress-side client endpoint;
- an egress-side server endpoint;
- isolated transport credentials and host keys;
- a stable SOCKS endpoint consumed by ingress Xray routing;
- healthchecks, updater ordering, snapshots, and rollback.
