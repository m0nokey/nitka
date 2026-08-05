# Nitka

`Nitka` is a local CLI orchestration tool that manages VPN infrastructure.

It provides centralized management of deployed VPN servers and their access
from one interface. You can add VPS nodes, configure them, check their status,
and issue or revoke VPN access keys without manually editing configuration
files.

`nitka` is not a VPN client. It manages the servers that user devices connect
to. A deployment can be a standalone Xray node or a logical Cascade made from
an ingress node and an egress node.

The module boundaries and lifecycle contracts are documented in
[docs/architecture.md](docs/architecture.md). To add a transport, follow the
[transport adapter checklist](docs/adding-transport.md).

The program runs on your macOS or Linux computer inside a Docker container.
Server data, SSH access, and VPN keys are stored only on your computer in a
local encrypted Vault. They are not sent to a cloud service and are not stored
in the project repository.

After the initial deployment, each VPS works autonomously. It runs Xray,
performs scheduled updates, checks the VPN stack, and recovers from a failed
Xray update.

It helps you:

```text
- deploy and manage standalone Xray VPN nodes
- deploy and manage two-server Cascaded VPNs
- create, rotate, and revoke VPN access keys
- choose which traffic uses the local or remote country
- block ads, trackers, malware, phishing, and other threats
- block selected countries
- open an SSH session without remembering ports or credentials
- update, restart, restore, or delete VPN servers
```

Public sanitized routing and client examples are documented in
[examples/README.md](examples/README.md).

> ⚠️ **Security Notice:**<br>
> Always review any script from the internet before running it on your system!

## Requirements

### Local computer

```text
- Docker
- Docker Compose
- Bash
- curl
- git
```

### Linux Docker access

`nitka` does not need to be started with `sudo`.

On Linux, the current user must have permission to access the Docker daemon.
If Docker reports a permission error for `/var/run/docker.sock`, configure
Docker access once:

```sh
sudo usermod -aG docker "$USER"
newgrp docker
docker info
```

If the Docker service is not running:

```sh
sudo systemctl enable --now docker
```

Do not run `sudo bash run.sh`, because this can make the local Vault and
controller files owned by `root`.

### VPS

```text
- Root/sudo access
- Supported OS: Debian 12+
- Public IPv4 address
- SSH password access for the first installation
- At least 1 vCPU and 1 GB RAM
```

## Quick Start

For normal use, download the latest stable release from the
[GitHub Releases page](https://github.com/m0nokey/nitka/releases/latest).
Release archives include a SHA-256 checksum and are the recommended way to run
`nitka`.

```sh
curl -fsSL --proto '=https' -O "https://github.com/m0nokey/nitka/releases/latest/download/nitka-latest.tar.gz" \
&& curl -fsSL --proto '=https' -O "https://github.com/m0nokey/nitka/releases/latest/download/SHA256SUMS" \
&& grep -F "nitka-latest.tar.gz" SHA256SUMS | sha256sum -c - \
&& tar -xzf nitka-latest.tar.gz \
&& cd "$(tar -tzf nitka-latest.tar.gz | sed -n '1s#/.*##p')" \
&& bash run.sh
```

The `releases/latest` link always points to the newest stable release. The
README does not need to be changed for every patch release.

For development and testing, use the `main` branch instead:

```sh
git clone https://github.com/m0nokey/nitka.git
cd nitka
bash run.sh
```

Without Git:

```sh
curl -fsSL https://github.com/m0nokey/nitka/archive/refs/heads/main.tar.gz | tar -xz
mv nitka-main nitka
cd nitka
bash run.sh
```

On the first run, create a password for the local encrypted Vault.

In the main menu choose:

```text
2. Add VPN server
```

Each input is shown on a separate screen. The previous screen is cleared.

```text
Enter VPS IP:
Enter VPS user [root]:
Enter VPS port [22]:
Enter VPS password:
```

```text
[!] Press Enter to use the default value shown in [brackets].
```

Before connecting, the manager shows the entered IP, user, port, and the
password status. Choose `2. Edit` if something is wrong.

Choose `1. Continue`. The manager then checks:

```text
- SSH access
- VPS resources
```

If the check succeeds, the manager asks for a camouflage domain:

```text
Add VPN server

This domain helps the VPN connection look like normal HTTPS traffic.
Use a real HTTPS website that supports TLS 1.3.
Press Enter to use the default value shown in [brackets].

Enter domain [github.com]:
```

After the domain screen, choose how VPN ports should be assigned:

```text
Configure VPN ports

Each VPN key gets two connection links: vision and xhttp.
If one link is blocked, use the other.
random ports are generated automatically.

1. vision: 443       xhttp: random
2. vision: random    xhttp: random [default]
3. vision: random    xhttp: 443
4. vision: manual    xhttp: manual
```

Press Enter to use the option marked `[default]`. Manual ports must be
different and must not overlap the generated SSH port.

Then choose an optional DNS protection profile:

```text
Block ads and threats

Optional. Blocks malware, phishing, scams, ads, trackers, and telemetry.
Current: disabled

1. Disabled  No blocking
2. Minimal   Malware protection
3. Optimal   Malware, phishing and scams
4. Full      Malware, ads and tracking
5. Maximum   Broad protection and DNS bypass
6. Custom    Choose protection categories

Not sure what to choose? Press Enter to keep it disabled.
You can enable it later from the VPN management menu.

?:
```

The default domain is `github.com`. You can change it according to your
country and camouflage strategy. The availability of DNS profiles depends on
the detected VPS CPU and memory resources.

After selecting a profile, wait for the deployment to finish.

### Creating a Cascade

Open `Add VPN server` and choose `Cascade VPN`. Enter the first VPS as egress
and the second as ingress, then
provide a dedicated external transport port for the egress endpoint. The
transport port must not be the management/bootstrap SSH port `22` (or any
other management port). The wizard stores the relationship and transport
credentials in the encrypted Vault, then deploys ingress and egress with their
role-specific playbooks.

The controller should have a direct management path to both VPS nodes while a
Cascade is being changed. Do not make deployment SSH depend on the VPN tunnel
that the deployment may restart.

During deployment, normal mode shows a short stage-based progress screen:

```text
Installing VPN server

  [ 10%] Checking the VPS connection                  done
  [ 20%] Checking VPS system                          done
  [ 30%] Preparing VPS access                         done
  [ 35%] Reconnecting after bootstrap                 done
  [ 40%] Hardening SSH access                         done
  [ 50%] Verifying hardened SSH access                done
  [ 60%] Installing Docker and system packages        done
  [ 70%] Rendering VPN configuration                  done
  [ 80%] Validating Xray and DNS configuration        done
  [ 90%] Starting and checking VPN stack              done
  [100%] VPN server added successfully.               done
```

The percentages represent deployment stages, not individual Ansible tasks.
They are intended to show what the manager is doing while the remote operation
is running.

After a successful deployment, the manager saves the VPN ports, keys, and
connection data in the encrypted Vault.

Each VPN access key includes two alternative Xray connection profiles:

- VLESS Vision with REALITY;
- VLESS XHTTP with REALITY in `packet-up` mode.

Both profiles are active and use separate ports. If one profile is blocked or
unstable on a network, you can connect using the other.

New nodes use randomly generated high TCP ports by default. You can instead use
TCP port `443` for VLESS TCP Vision and a generated high port for VLESS XHTTP, or
enter both ports manually during setup.

Existing nodes keep their current ports and are not changed automatically.

### Debug mode

If an operation fails and you need the technical output for an issue, run:

```sh
bash run.sh --debug
```

Debug mode shows the raw Ansible output in the terminal and keeps it visible
until you press Enter. This makes it possible to copy the failure details into
an issue. It does not create a permanent log file on the host. The normal mode
shows only the user-facing progress and result messages.

## Managing Multiple Nodes

One instance of `nitka` running in Docker can manage multiple VPS nodes.

Standalone nodes are listed individually. A Cascade is shown as one logical
entry with two related nodes: `Cascade ingress` is the public client-facing
Xray endpoint and `Cascade egress` is the remote exit/DNS node.

Each node still has its own management SSH connection, system services, Docker
project, health status, and rollback backup. The Cascade relationship is stored
in the Vault and is used to deploy and operate the pair in the correct order.

When the server list is checked, up to 16 nodes are checked concurrently. If
there are more nodes, the remaining checks wait for an available slot and run
automatically in the same refresh cycle.

Deployment, configuration changes, and removal are currently performed for one
selected node at a time. Autonomous updates run independently on every VPS.

The server list and status display are shown in the Server Menu below.

## How It Works

`nitka` runs in Docker on the user's computer and connects to VPS nodes over
management SSH. Ansible performs the initial server configuration and later
changes. After deployment, each node continues to operate independently.

```text
                         CONTROL PLANE

          ┌─────────────────────┐
          │ Nitka CLI           │
          │ encrypted Vault     │
          └─────────┬───────────┘
                    │
          management SSH / Ansible
                    ▼
          ┌─────────────────────┐
          │         VPNs        │
          └─────────────────────┘
           └────────────────────┘
            └───────────────────┘
             └──────────────────┘
```

Management SSH is the control channel. It must remain independent from the
VPN transport so a transport restart cannot remove the controller's recovery
path. Bootstrap SSH (the initial host access, normally port `22`) and the
dedicated transport port are separate settings and separate keys.

### Standalone Xray node

The original one-node Nitka deployment remains a supported mode. Its basic
runtime path is:

```text
  ┌─────────────────────────┐       SSH / Ansible       ┌─────────────────────────┐
  │ User's computer         │ ───────────────────────▶  │ VPS node                │
  │                         │                           │                         │
  │ nitka in Docker         │                           │ Debian + Docker Compose │
  │ encrypted local Vault   │                           │                         │
  └─────────────────────────┘                           │  ┌───────────────────┐  │
                                                        │  │ Xray              │  │
  ┌─────────────────────────┐       VPN connection      │  │ Vision + REALITY  │  │
  │ VPN client devices      │ ───────────────────────▶  │  │ XHTTP + REALITY   │  │
  │ phone / laptop / tablet │                           │  │ packet-up         │  │
  └─────────────────────────┘                           │  └─────────┬─────────┘  │
                                                        │            │            │
                                                        │            │ DNS        │
                                                        │            ▼            │
                                                        │  ┌───────────────────┐  │
                                                        │  │ Unbound           │  │
                                                        │  │ RPZ blocklists    │  │
                                                        │  │ NXDOMAIN          │  │
                                                        │  └─────────┬─────────┘  │
                                                        └────────────┬────────────┘
                                                                     │
                                                                 DNS-over-TLS
                                                          Cloudflare / AdGuard DNS
                                                                     │
                                                                     ▼
                                                                  Internet
```

Xray and Unbound are separate services in the standalone Docker Compose
project. When DNS protection is enabled, Xray uses the private Unbound
service; Unbound applies RPZ lists and forwards allowed queries over
DNS-over-TLS. The Cascade reuses the same Xray and DNS principles, but moves
the remote exit and transport server to a separate egress node.

## Cascaded VPN

A Cascade is one logical VPN service made from two VPS roles:

See the detailed [Cascade topology](ansible/roles/topology/cascade/README.md)
reference for the complete transport and blocking flow.

```text
                  Client
                    │
                    ▼
    ┌────────────────────────────────┐
    │ VPS [ingress node]             │   DIRECT by default
    │ local country                  │────────────────────▶ Internet [local exit]
    │ Xray + whitelist routing       │
    └────────────────┬───────────────┘
                     │
                     │ SSH TUN transport
                     │ 
                     ▼
    ┌────────────────────────────────┐
    │ VPS [egress node]              │   PROXY whitelist
    │ remote country                 │────────────────────▶ Internet [remote exit]
    │ SSH TUN server + Unbound DNS   │
    └────────────────────────────────┘
```

The ingress node is the only endpoint exposed to VPN clients. Xray uses
`DIRECT` by default; the routing policy sends selected domains, IP ranges, or
ports through the transport SOCKS endpoint. The egress node receives that
traffic, resolves remote DNS through its private Unbound service, applies the
selected RPZ protection, and performs the remote Internet exit.

This is not a full-tunnel requirement. The important property is that the
ingress policy decides which traffic needs the egress country. Ordinary traffic
can remain on the ingress/local path, while selected traffic gets the second
hop.

The user's computer does not need to stay powered on. After deployment, both
VPS nodes continue to run and maintain themselves.

## Transport Architecture

The Cascade is split into stable layers so a transport can be replaced without
rewriting Xray routing, DNS protection, access-key management, or system
hardening:

```text
  system_base       Debian, Docker, management SSH, timers, updater, rollback
        |
  topology/cascade/ingress  Xray, routing policy, client-side endpoint
  topology/cascade/egress   remote DNS, RPZ, server-side endpoint
        |
  transport         client endpoint ↔ server endpoint
```

The current deployed transport pair is Xray REALITY for client access and SSH
TUN for the Cascade backhaul:

```text
ansible/roles/transports/
  access/
    xray_reality/ current client-facing access adapter
  backhaul/
    ssh_tun/      current ingress-to-egress adapter
```

The concrete implementation files live in the canonical adapter and topology
roles. `transports/access` and `transports/backhaul` are dispatch boundaries;
`topology/cascade` owns only the ingress and egress composition. This keeps
future transport code out of topology orchestration.

Future transports such as NaiveProxy or Hysteria2 should implement the same
transport contract rather than duplicate the surrounding stack. Client access
and Cascade backhaul are separate selections: NaiveProxy can be added on the
client-facing side without coupling it to the backhaul, while Hysteria2 can
later be added as a backhaul adapter without changing Xray routing or DNS:

1. Define an ingress/client endpoint and an egress/server endpoint.
2. Keep transport credentials, host keys, and fingerprints in the encrypted
   Vault; never use the management key as a transport key.
3. Provide its Dockerfile, runtime configuration, healthcheck, and Compose
   service definition.
4. Expose one stable internal endpoint to the Xray/routing layer.
5. Provide an updater dependency chain and a restart/healthcheck procedure.
6. Support deployment snapshots and rollback before the endpoint is cut over.

The transport-specific implementation may change; the following layers should
not need to know whether the transport is SSH TUN, Naive, Hysteria2, or another
future implementation:

- Xray inbounds and access keys;
- routing and country policy;
- egress Unbound and RPZ profiles;
- management/bootstrap SSH;
- systemd updater timers and deployment rollback.

The role `transports/backhaul/ssh_tun` is the first concrete backhaul adapter.
When additional transports are introduced, they should use a parallel adapter
under `ansible/roles/transports/access/` or
`ansible/roles/transports/backhaul/`, selected by deployment state, while
preserving the same ingress/egress lifecycle and health contract.

## Main Menu

```text
1. VPN servers
2. Add VPN server
3. Vault

i. info
x. exit
?:
```

Use `i` to see help for the current screen. Use `b` to go back, `m` to return
to the main menu, and `x` to exit.

`Add VPN server` opens a second screen:

```text
1. Standalone VPN
2. Cascade VPN
```

The standalone wizard then lets you choose the client access transport:

```text
1. Xray REALITY
2. SSH proxy
```

`SSH proxy` is a fast temporary TCP proxy based on OpenSSH for clients such as
Shadowrocket. It is available for standalone nodes only. Native UDP is not
supported by OpenSSH. An optional external UDP relay uses UDP-over-TCP and may
be unstable for calls, games, and realtime audio. The standard SSH proxy
deployment does not include a UDP relay. The Cascade wizard uses Xray
REALITY for client access and SSH TUN for the ingress-to-egress backhaul.

SSH proxy capabilities:

```text
TCP proxy:   supported
Native UDP:  not supported
UDP relay:   best effort, UDP-over-TCP
```

The Cascade wizard selects two VPS nodes and assigns their roles:
the ingress node receives client connections, and the egress node provides the
transport exit and remote DNS.

## Server Menu

The VPS list shows the important information at a glance:

```text
  Node Management:

     IP              STATUS   COUNTRY   CREATED      MODE              PROVIDER

  1. 203.0.113.42    Active   RU        2026-05-05   Cascade ingress   Example Provider
  └─ 198.51.100.17   Active   DE        2026-05-05   Cascade egress    Example Provider
  2. 203.0.113.10    Active   NL        2026-05-06   Xray              Example Provider
  3. 203.0.113.11    Active   DE        2026-05-06   Xray              Example Provider
```

The Cascade is one selectable logical entry. Its second line is the egress
child, not a second independent VPN. The terminal uses color for quick
scanning: `Active` is green, `Partial` is yellow, and unavailable states are
red. The example documentation addresses and provider names are illustrative.

After selecting a VPS:

```text
1. Manage VPN server
2. Manage access keys

b. back
m. main
i. info
x. exit
?:
```

### Manage VPN Server

```text
1. Check VPN status
2. Open SSH session
3. Restart VPN server
4. Block ads and threats
5. Block countries
6. Rotate SSH key
7. Manage routing rules
8. Update Cascade
9. Replace VPS node
10. Delete VPN server
```

`Open SSH session` uses the saved management key and port from the Vault.
For a Cascade, operations that affect the transport or remote DNS are applied
to the appropriate node: ingress routing changes stay on ingress, while DNS
protection changes stay on egress. The controller keeps a deployment snapshot
and restores the last healthy stack if a cutover healthcheck fails.

`Replace VPS node` lets you replace either Cascade role. Replacing egress
deploys a new egress server, switches the existing ingress to it, verifies the
Cascade, and updates the Vault only after the replacement is healthy.
Replacing ingress keeps the existing client access keys and
routing policy while deploying the ingress replacement. The old node is
removed from the local Vault after a successful replacement; if it is
unreachable, delete it separately through the VPS provider.

Replacing a VPS changes its IP address but keeps the selected access and
backhaul transports. If DPI blocks a transport signature itself, replacing a
VPS IP is not enough; a separate transport migration is required.

### VPN Status

```text
Active           Xray is running and both VPN ports are reachable.
Partial          Xray is running and only one VPN port is reachable.
VPN unavailable  The VPS responded, but Xray is not confirmed running.
Unreachable      No VPN or management port responded.
```

## Autonomous Operation And Updates

After deployment, independent system services are installed on each VPS. They:

- update Debian and the node's Docker stack every night;
- reboot the VPS when a kernel update requires it;
- check the stack after an update;
- restore the previous working stack if an update fails.

Cascade nodes use distinct service names:

```text
cascade-os-updater.timer
cascade-ingress-docker-updater.timer
cascade-egress-docker-updater.timer
cascade-ingress-watchdog.timer
```

The ingress watchdog protects the Docker namespace that depends on the
transport client. Egress uses the updater and Compose healthchecks for its
transport server and Unbound stack. Management SSH is kept outside the
transport path so these services can be repaired from the control machine.

Both the server and client parts of Xray should be kept up to date. New versions
fix vulnerabilities, improve compatibility, and reduce the chance that outdated
protocol characteristics will be recognized and blocked by DPI.

For this reason, client applications on user devices should also be updated
after a server update. This does not guarantee that a connection will never be
blocked, but it is the safest and most reliable way to operate Xray.

## Optional Protection

### Block Ads And Threats

Disabled by default. You can enable lists that block known domain names used
for:

```text
- malware and dangerous websites
- phishing and scams
- ads and pop-ups
- trackers and email tracking
```

The selected lists are loaded on the VPS. Larger profiles need more VPS CPU
and RAM, so the manager checks resources before deployment.

```text
- Minimal: 1 vCPU / 1 GB RAM
- Optimal: 1 vCPU / 1 GB RAM
- Full: 2 vCPU / about 2 GB RAM
- Maximum: 2 vCPU / about 2.5 GB RAM
- Custom: depends on the selected lists
```

```text
- Disabled - no blocking
- Minimal - malware and dangerous websites
- Optimal - malware, phishing, and scams
- Full - malware, ads, trackers, and telemetry
- Maximum - broad protection and known DNS bypass services
- Custom - choose additional categories
```

Some legitimate websites or Smart TVs may be affected. You can change or
disable protection later from the server menu.

The lists are provided by the
[HaGeZi DNS Blocklists project](https://github.com/hagezi/dns-blocklists).

### Block Countries

Select one or more countries to block on the VPS. This is useful when a VPN
client cannot configure local traffic bypass directly.

The policy applies to all access keys on that VPS. It blocks destination IP
ranges assigned to the selected countries. For Russia, selecting `RU` also
matches `.ru` and `.рф` domains.

This is not a VPN detection guarantee. CDNs, shared hosting, and geolocation
data can cause false positives.

## Access Keys

```text
1. Show
2. Add
3. Delete
```

Each access key contains two paired client links:

```text
- VLESS TCP Vision — configured port
- VLESS XHTTP — configured port
```

Both ports are generated randomly by default. The port mode can be changed when
adding a node: use TCP `443` for Vision with a generated XHTTP port, or enter
both ports manually.

Deleting a key deletes both links together. Other keys are not changed.

## Vault

The Vault is a local encrypted file containing VPS access data, SSH keys, VPN
ports, REALITY keys, and access-key pairs.

The Vault is encrypted using the standard Ansible Vault format:

```text
$ANSIBLE_VAULT;1.1;AES256
```

The format uses:

- AES-256 in CTR mode;
- PBKDF2-HMAC-SHA256 for password-based key derivation;
- 10,000 PBKDF2 iterations;
- HMAC-SHA256 for ciphertext integrity;
- a salt stored in the encrypted Vault.

The Vault password is not stored on the user's computer or on any VPS. If the
password is lost, the Vault cannot be recovered.

Ansible Vault protects data at rest. SSH protects the connection while the
controller communicates with a VPS.

```text
1. Change encryption password
2. Backup encrypted state
3. Restore encrypted state
4. View backups
5. Delete Vault
```

The Vault is stored at:

```text
$HOME/.local/state/nitka/vault.json
```

The Vault password is not stored on the VPS and cannot be recovered from the
encrypted file.

Before each successful Vault replacement, the previous encrypted file is saved
as an automatic recovery copy in `backups/system/`. The newest 20 copies are
kept and older copies are deleted automatically. `Backup encrypted state`
creates a separate user-created `tar.gz` archive in `backups/user/`; user archives
are not part of the automatic rotation. `View backups` lists user archives
with their UTC timestamp and full path. Automatic recovery copies are internal
and are not shown. `Restore encrypted state` lets you select a user archive
by number, so you do not need to enter a path manually.

If the Vault file is damaged or decrypts to invalid state, nitka does not
delete it or create an empty replacement. It moves the original to a timestamped
`.corrupt.*` file, keeps it protected with owner-only permissions, and stops
until a valid backup is restored.

The complete local structure is:

```text
$HOME/.local/state/nitka/
├── vault.json
└── backups/
    ├── user/     user-created encrypted archives
    └── system/   automatic recovery copies
```

### Restore On Another Computer

Use a user backup when moving the Vault to another computer.

1. On the old computer, choose `Vault` and `Backup encrypted state`.
2. Copy the created `vault-*.tar.gz` file to this directory on the new computer:

```text
$HOME/.local/state/nitka/backups/user/
```

3. Start nitka and choose `Vault` and `Restore encrypted state`.
4. Select the backup by number and enter the Vault password when requested.

Create the `backups/user` directory first if it does not exist. The Vault does not
need to be initialized before restoring a backup. With the Docker launcher,
`/state/nitka` is the path inside the container; the host path above is the
directory to use for copying files. The password is not included in the
backup and must be remembered separately.

The `.local` directory is hidden in most file managers. Use the terminal to
copy the backup to a visible folder before transferring it:

```bash
ls -lh "$HOME/.local/state/nitka/backups/user/"
cp "$HOME/.local/state/nitka/backups/user"/vault-*.tar.gz "$HOME/Downloads/"
```

After copying the file to the new computer, place it into the Vault backup
directory:

```bash
mkdir -p "$HOME/.local/state/nitka/backups/user"
cp "$HOME/Downloads"/vault-*.tar.gz "$HOME/.local/state/nitka/backups/user/"
chmod 600 "$HOME/.local/state/nitka/backups/user"/vault-*.tar.gz
```

Then start nitka, open `Vault`, choose `Restore encrypted state`, and select
the user backup by number.

Automatic files in `backups/system/` are intended for internal local recovery.
They are not part of the user backup browser; use a user `tar.gz` backup for
recovery and migration.

### Repeatable Use Without Keeping The Repository

After a VPS has been deployed, it does not depend on the local project directory
or the local `nitka` Docker image. You may remove the cloned repository and
the local image without interrupting the VPN on the VPS.

Do not delete the local state directory:

```text
$HOME/.local/state/nitka/
```

This directory contains the encrypted Vault with the infrastructure state.

When you need to issue new keys, change settings, or remove a VPS, download the
latest stable release archive and run it again. You do not need to keep the
repository or the local controller image between runs.

Use the same verified Release installation commands from `Quick Start` above.

`nitka` finds the existing Vault on the computer and asks for its password.
After unlocking it, your VPS nodes, SSH access, VPN keys, and infrastructure
settings become available again.

## Delete A VPN Server

`Delete VPN server` cleans the VPS before deleting its Vault record.

```text
- stops and deletes the Xray Docker stack
- deletes updater services and Xray files
- deletes the management user created by nitka
- restores the original SSH configuration
- deletes the server from the local Vault after successful cleanup
```

If remote deletion fails, the Vault entry is kept so the operation can be
retried.

## Software Sources And Supply Chain

The project uses official repositories and upstream project sources instead of
arbitrary binaries or unverified installation scripts.

- The local controller runs from the official `alpine:3.23` image.
- Ansible, OpenSSH, Python, and supporting tools are installed from Alpine
  repositories.
- VPS system packages are installed from the official Debian and Debian
  Security repositories.
- Docker Engine, Docker CLI, and the Compose plugin are installed from Docker's
  official Debian APT repository and verified with Docker's GPG key.
- Xray runs from the upstream image `ghcr.io/xtls/xray-core:latest`.
- Unbound is built on the VPS from the official `alpine:3.23` base image, with
  the Unbound package installed from Alpine repositories.
- DNS protection lists are downloaded from their upstream projects, including
  HaGeZi, AdGuard, URLhaus, and ThreatFox.

Repository sources and signatures are configured by Ansible during deployment
and are used again during automatic updates.

Starting with v0.2.10, every release is published only after the full CI
pipeline succeeds. Release assets include SPDX SBOMs for the controller,
Unbound, and Xray runtime images. GitHub artifact attestations record the build
provenance of the release assets and checksum manifest.

### Floating Runtime Dependencies

Runtime dependencies intentionally track supported upstream versions instead
of being permanently pinned. This allows installations to continue receiving
compatibility and security updates if maintenance of `nitka` stops.

The current runtime model includes the upstream Xray image tag, current Alpine
packages, the `community.docker` Ansible collection, Debian packages, and
upstream DNS protection lists. CI vulnerability scanning, image smoke tests,
VPN health checks, and automatic rollback reduce the risks of upstream changes.

This is a deliberate supply-chain trade-off: floating dependencies improve
long-term compatibility and unattended security updates, but reduce
reproducibility and depend on upstream release quality. See
[`SECURITY.md`](SECURITY.md) and the [threat model](docs/THREAT_MODEL.md) for
the detailed assumptions and accepted risks.

## Technical Overview

A standalone Xray node runs:

```text
- VLESS TCP with Vision and REALITY
- VLESS XHTTP with REALITY in packet-up mode
```

In a Cascade, ingress runs the Xray client-facing stack and egress runs the
transport server plus Unbound. Xray routing selects `DIRECT` or the internal
transport SOCKS endpoint. Egress Unbound uses configured encrypted upstreams
and optional RPZ blocklists; blocked domain names return `NXDOMAIN`.

The local Vault is the source of truth for VPS access and VPN keys. No
separate key database is created on the VPS.

## Repository Layout

```text
.
├── run.sh
│   User-facing launcher and the only supported entrypoint.
│   Start the project from the repository root with:
│   bash run.sh
│
├── controller/
│   ├── Dockerfile
│   │   Docker image for the local controller.
│   ├── compose.yml
│   │   Docker Compose definition for the controller container.
│   └── entrypoint.sh
│       Internal controller entrypoint executed inside Docker.
│
├── lib/
│   Focused Bash runtime modules for UI navigation, Vault handling,
│   node management, deployment, DNS, security, access keys, and pipelines.
│
├── ansible/
│   ├── playbooks/
│   │   ├── preflight.yml         # prerequisites and input validation
│   │   ├── bootstrap.yml         # initial host access
│   │   ├── harden_ssh.yml        # temporary SSH transition
│   │   ├── finalize_ssh.yml      # final management SSH cutover
│   │   ├── deploy_standalone.yml # standalone composition
│   │   ├── deploy_cascade.yml    # Cascade composition
│   │   ├── manage_management_ssh.yml
│   │   ├── remove.yml            # uninstall and restore
│   │   └── rollback_cascade_*.yml
│   └── roles/
│       ├── system_base/          # Debian, Docker, SSH, timers, updates
│       ├── topology/cascade/      # ingress/egress composition only
│       └── transports/
│           ├── access/            # Xray REALITY and SSH proxy adapters
│           └── backhaul/          # SSH TUN and future backhauls
│
├── examples/
│   └── cascade/
│       ├── clients/shadowrocket/ # Cascade client format
│       └── routing/xray/          # Cascade routing example
│
├── scripts/
│   Python helpers for encrypted state validation, node rendering,
│   access-key rendering, and local output generation.
│
├── tests/
│   Navigation, state, Vault recovery, Ansible template, and rendering tests.
│
├── data/
│   Static project data used by the controller.
│
├── .github/
│   ├── ci/
│   │   CI-only configuration, including Trivy.
│   └── workflows/
│       GitHub Actions for checks, tests, security scanning, and releases.
│
├── docs/
│   Additional project documentation, including the threat model.
│
├── README.md
│   User documentation and technical overview.
│
├── SECURITY.md
│   Security policy and vulnerability reporting instructions.
│
└── .dockerignore
    Files excluded from the controller image build.
```

## License

MIT. See [LICENSE](./LICENSE).
