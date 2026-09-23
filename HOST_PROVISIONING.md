# Host provisioning: from a bare VM to "ready for DEPLOYMENT.md"

This is the missing prerequisite step before DEPLOYMENT.md's "Initial deploy (production)": a
**bare** Linux VM with no Docker, no git, nothing STIR-specific installed yet. It covers **Rocky
Linux 9** as the primary target; every step also gives the **Ubuntu** (22.04/24.04) equivalent
where the procedure differs. Once this document ends, continue at DEPLOYMENT.md step 2
(`python scripts/initialize.py`).

Nothing here is STIR-specific beyond the exact ports and files STIR's compose stack needs -
it is standard Docker Engine + Compose plugin provisioning for each distribution. Follow the
steps in order - step 6 (SELinux) intentionally comes *after* cloning the repositories in step 5,
because it relabels a file that only exists once they're cloned.

## 0. Sizing

STIR's own compose stack has no separately documented minimum; consider the following before
provisioning:

- Runtime footprint: four JVM services (Shell 512 MB heap, STIR/osTRIS/Ledger production 384–512
  MB heap each) plus PostgreSQL 17, MinIO, Caddy and three static-file nginx UIs. Comfortable
  runtime alone fits in ~4 GB RAM.
- **Build** footprint is the real constraint: `docker compose build --no-cache` compiles four
  Spring Boot/Maven modules and can transiently use significantly more RAM/CPU than running the
  stack afterward, especially the first build (no local Maven/npm cache yet).
- Recommended minimum: **4 vCPU / 8 GB RAM / 40 GB disk** (SSD). Smaller (2 vCPU / 4 GB) can work
  for runtime only if you build images elsewhere and only `docker compose up -d` (no `--build`)
  on the target VM.
- A swap file is not required but is cheap insurance on memory-constrained VMs during the build
  step; see step 4.

## 1. Update the base system

Rocky 9:
```sh
sudo dnf -y update
sudo dnf -y install dnf-plugins-core curl git
```

Ubuntu:
```sh
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get -y install ca-certificates curl gnupg git
```

## 2. Install Docker Engine + the Compose plugin

STIR's scripts always invoke `docker compose` (the plugin, v2 syntax) - never the legacy
standalone `docker-compose` binary. Install Docker's own packages, not the distribution's
`docker`/`podman-docker` package, which on both Rocky and Ubuntu is either absent, outdated, or
(on Rocky) actually Podman under a compatibility shim - none of that includes the Compose plugin
STIR needs.

### Rocky Linux 9

```sh
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Rocky has no `docker.io`/`docker-ce` package of its own; the upstream **CentOS** repo is the
correct and officially supported source for RHEL-family distributions including Rocky 9 - there
is no separate `.../linux/rocky/` repo.

### Ubuntu 22.04 / 24.04

```sh
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

### Both: run Docker as a non-root operator (recommended)

```sh
sudo usermod -aG docker "$(whoami)"
```
Log out and back in (or `newgrp docker`) for the group membership to take effect, then verify:
```sh
docker run --rm hello-world
docker compose version
```
If you are operating as `root` directly (common on a fresh cloud VM, e.g. an interactive
`[root@localhost ~]#` prompt), this step is unnecessary - root already has Docker access - but
still run the two verification commands.

## 3. Firewall

STIR's production overlay (`compose.production.yml`) publishes **only** `80/tcp`, `443/tcp` and
`443/udp` (Caddy; HTTP→HTTPS redirect + automatic Let's Encrypt + HTTP/3). Every application/DB
port is bound to `127.0.0.1` only and is never meant to reach the public firewall.

### Rocky Linux 9 (firewalld)

The full Rocky 9 ISO/DVD install ships firewalld enabled by default, but many **minimal** and
cloud-provider VM images (the "minimal"/cloud-init variants most VPS providers boot) do not
install it at all - `firewall-cmd: command not found` means exactly that, not a misconfiguration.
Check first, and install it if missing:
```sh
command -v firewall-cmd || sudo dnf -y install firewalld
sudo systemctl enable --now firewalld
firewall-cmd --state   # expect "running"
```
Then open the ports:
```sh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --permanent --add-port=443/udp
sudo firewall-cmd --reload
sudo firewall-cmd --list-all
```
firewalld's default `public` zone already includes `ssh`, so a freshly installed firewalld
normally does not lock out your current session - confirm with `firewall-cmd --list-all` (look
for `ssh` under `services`) before trusting that, and add it explicitly if it is missing:
`sudo firewall-cmd --permanent --add-service=ssh`.

### Ubuntu (ufw, if you use it - many cloud images ship it disabled and rely on the cloud
provider's own security group instead; check `sudo ufw status` first)

```sh
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable   # only if ufw was not already active - enabling it can also block SSH if you
                   # skip the OpenSSH rule above
```

If the VM sits behind a cloud provider's own security group/network firewall (most managed VMs
do), open the same three ports there as well - the host-level firewall alone is not sufficient.

## 4. Optional: swap (memory-constrained VMs only)

If the VM has 4 GB RAM or less, add swap before the first `docker compose build`:
```sh
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
Identical on Rocky and Ubuntu. Skip this on an 8 GB+ VM.

## 5. Python 3, OpenSSL, and cloning STIR

`scripts/initialize.py` needs `python3`, `git` and `openssl` on the host (not inside a
container). Both distributions ship python3 by default; confirm and install the rest:

Rocky 9:
```sh
python3 --version   # 3.9+ ships by default on Rocky 9
sudo dnf -y install openssl
```

Ubuntu:
```sh
python3 --version   # 3.10+ on 22.04, 3.12+ on 24.04
sudo apt-get -y install python3 openssl
```

Then clone the five repositories as siblings, exactly as in stir-main's own README quick start
(`stir-workspace` as the parent, four siblings inside it). Pick a real working directory first
(e.g. `/opt/stir` or a non-root operator's home) - do not leave it at `~` of an interactive root
shell without thinking about it, since later steps (and DEPLOYMENT.md) assume you `cd` into
`stir-main` and stay there:
```sh
cd /opt   # or wherever you want the workspace to live
git clone https://github.com/toni-soler/stir-workspace.git stir
cd stir
git clone https://github.com/toni-soler/stir-doc.git
git clone https://github.com/toni-soler/stir-backend.git
git clone https://github.com/toni-soler/stir-frontend.git
git clone https://github.com/toni-soler/stir-main.git
cd stir-main
pwd   # confirm you are inside .../stir/stir-main before continuing to step 6
```

## 6. SELinux (Rocky-specific; Ubuntu uses AppArmor and normally needs no action here)

Rocky 9 ships SELinux **Enforcing** by default (`getenforce`). Docker Engine itself works fine
under Enforcing, but `compose.production.yml` bind-mounts a real host file read-only into the
`proxy` container:
```yaml
volumes:
  - ./deploy/Caddyfile.production:/etc/caddy/Caddyfile:ro
```
Under Enforcing SELinux, a bind-mounted file that does not carry a container-accessible context
can make Caddy fail to read it (`permission denied` in `docker compose logs proxy`, even though
the Unix file permissions look correct). Run this **from inside `stir-main`** (step 5 above must
already be done - `deploy/Caddyfile.production` must exist as a relative path from where you run
this, or `restorecon` fails with "No such file or directory"):
```sh
pwd   # must end in .../stir/stir-main
sudo dnf -y install policycoreutils-python-utils   # provides semanage, if not already present
sudo semanage fcontext -a -t container_file_t "$(pwd)/deploy/Caddyfile.production"
sudo restorecon -v deploy/Caddyfile.production
```
Do **not** disable SELinux or set it to Permissive as a shortcut - relabeling the one file that
needs it is the correct, minimal fix and does not touch the tracked compose file itself (Ubuntu
needs none of this, since AppArmor does not gate bind-mount reads the same way).

If you already ran `semanage fcontext -a` with the wrong path (e.g. from your home directory
before cloning), remove the stale rule so it does not linger:
```sh
sudo semanage fcontext -d -t container_file_t "<the wrong path you used>"
```

## 7. DNS

Before running `docker compose up` with the production overlay, point the domain you will use in
`STIR_PUBLIC_HOSTNAME`/`STIR_PUBLIC_BASE_URL` (DEPLOYMENT.md step 4) at this VM's public IP with
an A record (and AAAA if the VM has IPv6) - Caddy's automatic HTTPS performs an ACME HTTP-01/
TLS-ALPN challenge against that hostname on ports 80/443 and will fail to obtain a certificate if
DNS does not resolve to this host yet. Propagation can take minutes to hours depending on your
registrar/TTL; confirm with `dig +short <your-domain>` (or `nslookup`) from outside the VM before
proceeding.

## 8. Continue at DEPLOYMENT.md

At this point the VM has Docker Engine + Compose plugin running, the firewall open on 80/443(+
udp), the five STIR repositories cloned as siblings, and (Rocky) SELinux accounted for. Continue
at `stir-main/DEPLOYMENT.md`, "Initial deploy (production)", starting from step 2
(`python scripts/initialize.py`) - you are already in `stir-main`, so step 2 is next.
