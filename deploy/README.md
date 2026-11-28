# Putting the full app on a server

The app needs a host that can start Docker containers, because every stage of generated
code runs in the sandbox. This guide uses a small Arm VM on Microsoft Azure, which is where
the public demo runs: https://pipeline-builder.westus2.cloudapp.azure.com

An Azure for Students account gives $100 of credit with no payment card. The VM below costs
about $25 a month when left on, so the credit lasts roughly three months (prices as of
November 2026; check the size's price in the portal). For a host that stays free, see
"Other hosts" at the end.

## What visitors can do

- Open the site, browse past runs, and start a build with your API key.
- Builds stop for the day once `ADPB_DAILY_USD` is used up. Each run still has its own limit.
- With `ADPB_PRIVATE_RUNS=1` each browser has its own runs, memory and database. A
  visitor cannot list, open or approve anyone else's run. There are no accounts: the
  browser is recognised by a cookie, so clearing cookies starts a visitor afresh.
- You, with SSH access to the server, can read every visitor's files under `var/visitors/`.

## Steps

1. **Create the VM** (Azure portal, Virtual machines, Create):
   - Image: Ubuntu Server 24.04 LTS, Arm64.
   - Size: `Standard_B2pls_v2` (2 vCPUs, 4 GB). Set **Security type** to **Standard**
     first; the Arm B-series sizes are not offered under "Trusted launch".
   - Region: one your subscription allows. A student subscription is limited to a few
     regions, and a size shows as "not available" in the others. To see the list, open
     Policy, Assignments, "Allowed resource deployment regions".
   - Authentication: SSH public key. Inbound ports: SSH (22), HTTP (80) and HTTPS (443).
   - Disk: 30 GB Standard SSD.
2. **Give the address a name** (needed for HTTPS, and free): open the VM's public IP
   address, Settings, Configuration, type a **DNS name label** and Apply. The site is then
   reachable at `<label>.<region>.cloudapp.azure.com`.
3. **SSH in and get the code** (the login user is the one you chose, `azureuser` by default):
   ```bash
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/Harshil411/autonomous-data-pipeline-builder.git
   cd autonomous-data-pipeline-builder
   ```
4. **Add your key and the cap**. Create `.env` on the server (it is never committed):
   ```bash
   cat > .env <<'ENV'
   ANTHROPIC_API_KEY=paste-your-key-here
   ADPB_DAILY_USD=1.00
   ADPB_PRIVATE_RUNS=1
   ENV
   chmod 600 .env
   ```
   Use a key made only for this server, with its own spend limit set in the Claude
   console. The daily cap is the app's guard; the console limit is the backstop.
5. **Run the setup**, with the name from step 2:
   ```bash
   SITE_DOMAIN=<label>.<region>.cloudapp.azure.com bash deploy/setup-vm.sh
   ```
   Caddy fetches a Let's Encrypt certificate for that name and renews it by itself. Anyone
   who opens the bare IP address is sent to the secure name. Without `SITE_DOMAIN` the
   script serves plain HTTP on the IP address, which browsers mark "Not secure".
6. **Open** `https://<label>.<region>.cloudapp.azure.com` and run the shop-orders example.
   It reaches the approval step in about a minute on this size (81 seconds on a laptop).

Visitors are recognised by a cookie tied to the site's address, so moving from the IP
address to a name starts everyone afresh; earlier runs stay on disk under `var/visitors/`.

## Day to day

| Task | Command |
|---|---|
| See if it is running | `systemctl status pipeline-builder` |
| Watch the log | `journalctl -u pipeline-builder -f` |
| Update to the latest code | `git pull && uv sync --no-dev && sudo systemctl restart pipeline-builder` |
| Change the daily cap | edit `.env`, then `sudo systemctl restart pipeline-builder` |
| Clear old runs | `rm -rf var/visitors` |

## Other hosts

The setup script works on any Debian or Ubuntu server with a public address.

**Oracle Cloud Always Free** is the option that stays free: an Arm VM
(`VM.Standard.A1.Flex`, 2 OCPUs and 12 GB when this was written; check Oracle's "Always
Free Resources" page). Use Ubuntu 24.04, and open ports 80 and 443 in the subnet's security
list (source `0.0.0.0/0`, TCP); the setup script opens the same ports in the server's own
firewall. The login user is `ubuntu`. "Out of host capacity" means try another availability
domain or try later. Oracle may reclaim a VM that stays nearly idle for seven days. This
route is written from Oracle's documentation; the script's firewall step has not been run
on an Oracle VM.

**Google Cloud's free `e2-micro`** (1 GB) works but is slow. Use a free-tier US region with
Debian 12 and a 30 GB standard disk, tick "Allow HTTP traffic" and "Allow HTTPS traffic",
and set a budget alert. Measured on an `e2-micro` shared with another small site: the
shop-orders example finished in about six and a half minutes, with no out-of-memory kills.
On a server that small, or one that already hosts another site:

- Add these to `.env` to save memory:
  ```
  ADPB_SANDBOX_MEMORY=300m
  ADPB_EMBEDDINGS=hash
  ```
  `hash` swaps the memory search's language model for a lightweight one, so it matches
  past fixes less well.
- If ports 80 and 443 are taken, skip Caddy: run Streamlit on its own port
  (`--server.address 0.0.0.0 --server.port 8080`) and open that port in the firewall.
- Protect the other site by adding `MemoryHigh=350M`, `MemoryMax=500M` and
  `OOMScoreAdjust=600` to the `[Service]` section of `pipeline-builder.service`.
