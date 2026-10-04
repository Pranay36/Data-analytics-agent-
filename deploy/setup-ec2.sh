#!/bin/sh
# Run once on a fresh Ubuntu 24.04 EC2 machine, as the default user (ubuntu):
#     sh setup-ec2.sh
# Installs Docker, adds some swap (a 2 GB machine needs it), and makes the deploy folder.
set -eu

echo "== installing Docker"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"

echo "== adding 2 GB swap"
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

echo "== creating /opt/insightflow"
sudo mkdir -p /opt/insightflow/deploy
sudo chown -R "$USER":"$USER" /opt/insightflow

echo
echo "Done. Log out and back in (so the docker group applies), then create"
echo "/opt/insightflow/deploy/.env from deploy/.env.prod.example."
