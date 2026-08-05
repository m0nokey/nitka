# Transport adapters

Transport adapters are split by their position in the network path:

```text
access   client device → ingress VPS
backhaul ingress VPS → egress VPS
```

An adapter owns only its transport-specific endpoint, credentials, runtime,
healthcheck, Compose wiring, update dependency, and rollback behavior. It must
expose a stable internal endpoint to the topology and routing layers.

The backhaul adapter publishes the runtime contract consumed by the Cascade
roles. The contract contains service and container names, build context, proxy
and DNS endpoints, and endpoint-specific network values. Topology templates use
that contract instead of assuming SSH TUN names, so a future backhaul adapter
can replace the endpoint without rewriting the Cascade topology.

The current deployment uses:

```yaml
topology: cascade
access:
  transport: xray-reality
backhaul:
  transport: ssh-tun
```

The client-side public examples are deliberately separate from this tree.
Supported access adapters are Xray REALITY and the standalone SSH dynamic
proxy. Shadowrocket can use the SSH adapter with public-key authentication.

Future adapters are added as parallel directories. They must implement the
same contract before they can be selected by deployment state. A transport
migration is separate from node replacement: it validates the new access and
backhaul pair, deploys both endpoints, runs healthchecks, and writes the new
pair to the Vault only after the cutover succeeds.
