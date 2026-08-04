# Transport adapters

Transport adapters are split by their position in the network path:

```text
access   client device -> ingress VPS
backhaul ingress VPS -> egress VPS
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

The client-side public example is deliberately separate from this tree. The
only documented client is Shadowrocket for iOS/macOS; the server access
adapter remains Xray REALITY.

Future adapters are added as parallel directories. They must implement the
same contract before they can be selected by deployment state.
