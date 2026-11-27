# Putting the full app on a free server

The app needs a host that can start Docker containers, because every stage of generated
code runs in the sandbox. This guide uses Google Cloud's always-free `e2-micro` VM. It has
about 1 GB of memory, so the setup adds swap and the app allows one build at a time.

## What visitors can do

- Open the site, browse past runs, and start a build with your API key.
- Builds stop for the day once `ADPB_DAILY_USD` is used up. Each run still has its own limit.
- With `ADPB_PRIVATE_RUNS=1` each browser has its own runs, memory and database. A
  visitor cannot list, open or approve anyone else's run. There are no accounts: the
  browser is recognised by a cookie, so clearing cookies starts a visitor afresh.
- You, with SSH access to the server, can read every visitor's files under `var/visitors/`.

## Steps

1. **Create the VM** (Google Cloud console, Compute Engine): machine type `e2-micro`, a
   free-tier US region (`us-west1`, `us-central1` or `us-east1`), Debian 12, a 30 GB
   standard disk, and tick "Allow HTTP traffic" and "Allow HTTPS traffic".
2. **Set a budget alert** in Billing so you hear about any charge. Network traffic beyond
   the free allowance is billed.
3. **SSH in and get the code**:
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
5. **Run the setup**:
   ```bash
   bash deploy/setup-vm.sh                          # plain HTTP on the VM's IP address
   SITE_DOMAIN=demo.example.com bash deploy/setup-vm.sh   # HTTPS, once a domain points here
   ```
6. **Open** `http://<the VM's external IP>` and run the shop-orders example.

## Day to day

| Task | Command |
|---|---|
| See if it is running | `systemctl status pipeline-builder` |
| Watch the log | `journalctl -u pipeline-builder -f` |
| Update to the latest code | `git pull && uv sync --no-dev && sudo systemctl restart pipeline-builder` |
| Change the daily cap | edit `.env`, then `sudo systemctl restart pipeline-builder` |
| Clear old runs | `rm -rf var/visitors` |

## On a 1 GB server, or one that already hosts another site

Measured on an `e2-micro` shared with another small site: the shop-orders example finished
in about six and a half minutes (81 seconds on a laptop), with no out-of-memory kills.

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
