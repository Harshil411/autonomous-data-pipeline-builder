#!/usr/bin/env bash
# One-time setup of a Debian/Ubuntu server (Oracle Cloud's free Arm VM, or Google Cloud's
# free e2-micro). Run it as your normal login user, from the repository root:
#   bash deploy/setup-vm.sh
#
# It installs Docker, uv and Caddy, adds swap on a small-memory machine, opens the host
# firewall for web traffic, builds the sandbox image, and runs the app as a service behind
# HTTPS. It does not touch your API key: put that in .env yourself (see deploy/README.md)
# before or after running this. It is safe to run again.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
USER_NAME=$(id -un)

echo "== swap (2 GB, only when memory is under 4 GB) =="
MEM_MB=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 4000 ] && ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== host firewall =="
# Oracle Cloud's images reject everything except SSH in iptables, in addition to the cloud
# security list. Let web traffic in, now and after a reboot. Other hosts have no such rule.
RULES=/etc/iptables/rules.v4
for port in 80 443; do
  if sudo iptables -S INPUT 2>/dev/null | grep -q -- '-j REJECT' \
     && ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    line=$(sudo iptables -L INPUT -n --line-numbers | awk '$2 == "REJECT" {print $1; exit}')
    sudo iptables -I INPUT "$line" -p tcp --dport "$port" -j ACCEPT
  fi
  if sudo test -f "$RULES" && sudo grep -q -- '^-A INPUT -j REJECT' "$RULES" \
     && ! sudo grep -q -- "--dport $port -j ACCEPT" "$RULES"; then
    sudo sed -i "/^-A INPUT -j REJECT/i -A INPUT -p tcp -m state --state NEW -m tcp --dport $port -j ACCEPT" "$RULES"
  fi
done

echo "== packages =="
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y docker.io caddy git curl
sudo usermod -aG docker "$USER_NAME"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "== app and sandbox image =="
uv sync --no-dev
sudo docker build -t adpb-sandbox:0.1 sandbox/

echo "== service =="
sudo tee /etc/systemd/system/pipeline-builder.service >/dev/null <<UNIT
[Unit]
Description=Pipeline Builder (Streamlit)
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=$USER_NAME
SupplementaryGroups=docker
WorkingDirectory=$REPO
ExecStart=$HOME/.local/bin/uv run --no-dev streamlit run app/Home.py --server.address 127.0.0.1 --server.port 8501 --server.maxUploadSize 5
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now pipeline-builder

echo "== HTTPS =="
if [ -n "${SITE_DOMAIN:-}" ]; then
  printf '%s {\n  reverse_proxy 127.0.0.1:8501\n}\n' "$SITE_DOMAIN" | sudo tee /etc/caddy/Caddyfile >/dev/null
else
  printf ':80 {\n  reverse_proxy 127.0.0.1:8501\n}\n' | sudo tee /etc/caddy/Caddyfile >/dev/null
  echo "No SITE_DOMAIN given: serving plain HTTP on port 80. Re-run with SITE_DOMAIN=your.domain for HTTPS."
fi
sudo systemctl restart caddy

echo "Done. Check with:  systemctl status pipeline-builder   and   journalctl -u pipeline-builder -f"
