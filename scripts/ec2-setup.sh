#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/insightflow}"
GIT_REPO_URL="${GIT_REPO_URL:-}"
GIT_BRANCH="${GIT_BRANCH:-main}"

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "== Docker + Compose already installed — skipping"
    docker --version
    docker compose version
    return 0
  fi

  echo "== Installing Docker + Compose plugin"
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  else
    echo "ERROR: cannot detect OS; install Docker manually"
    exit 1
  fi

  case "${ID:-}" in
    ubuntu|debian)
      sudo apt-get update -qq
      sudo apt-get install -y ca-certificates curl gnupg
      sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      sudo chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
      sudo apt-get update -qq
      sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
    amzn|amazon)
      sudo yum update -y -q
      sudo yum install -y docker
      sudo systemctl enable --now docker
      # Compose plugin on Amazon Linux
      sudo mkdir -p /usr/local/lib/docker/cli-plugins
      COMPOSE_VERSION="v2.24.5"
      sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
      sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
      ;;
    *)
      echo "ERROR: unsupported OS '${ID:-unknown}'. Install Docker manually."
      exit 1
      ;;
  esac

  sudo usermod -aG docker "${USER}" 2>/dev/null || true
  echo "Docker installed"
  docker --version
  docker compose version
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sudo docker "$@"
  fi
}

ensure_repo() {
  if [ -d "$APP_DIR/.git" ]; then
    echo "== Repo already cloned at $APP_DIR — skipping clone"
    return 0
  fi

  if [ -z "$GIT_REPO_URL" ]; then
    echo "ERROR: GIT_REPO_URL is not set and $APP_DIR does not exist"
    exit 1
  fi

  echo "== Cloning $GIT_REPO_URL → $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"

  CLONE_URL="$GIT_REPO_URL"
  if [ -n "${EC2_GIT_TOKEN:-}" ]; then
    CLONE_URL="${GIT_REPO_URL/https:\/\//https:\/\/x-access-token:${EC2_GIT_TOKEN}@}"
  fi

  git clone --branch "$GIT_BRANCH" --depth 1 "$CLONE_URL" "$APP_DIR"
  echo "Clone done"
}

ensure_env() {
  umask 077

  if [ -n "${EC2_ENV_FILE_B64:-}" ]; then
    echo "== Writing .env from EC2_ENV_FILE_B64 (base64 decode)"
    printf '%s' "$EC2_ENV_FILE_B64" | base64 -d >"$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    return 0
  fi

  if [ -n "${EC2_ENV_FILE:-}" ]; then
    echo "== Writing .env from EC2_ENV_FILE secret"
    printf '%s\n' "$EC2_ENV_FILE" >"$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    return 0
  fi

  if [ -f "$APP_DIR/.env" ]; then
    echo "== .env already exists on server — keeping it"
    return 0
  fi

  echo "ERROR: no .env on server."
  echo "Set EC2_ENV_FILE (raw) or EC2_ENV_FILE_B64 (base64) as a repository secret."
  echo "Encode locally:  base64 -w0 .env   (Linux)  or  base64 -i .env  (macOS)"
  exit 1
}

ensure_docker
ensure_repo
ensure_env

echo "== Setup complete"
