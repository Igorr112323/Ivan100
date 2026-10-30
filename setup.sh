#!/usr/bin/env bash
# Установка «АКВАЧЕК» на чистый VPS (Ubuntu/Debian).
#
#   sudo ./setup.sh                 — поднять на http://IP:8080
#   sudo DOMAIN=akvachek.ru ./setup.sh — плюс nginx и бесплатный HTTPS
#
set -euo pipefail

REPO="${REPO:-https://github.com/Igorr112323/Ivan100.git}"
BRANCH="${BRANCH:-arena/25308257-ivan100}"
DIR="${DIR:-/opt/akvachek}"
PORT="${PORT:-8080}"
DOMAIN="${DOMAIN:-}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

say "Устанавливаю Docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
fi
apt-get install -y docker-compose-plugin 2>/dev/null || true

say "Получаю код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --all
  git -C "$DIR" checkout "$BRANCH"
  git -C "$DIR" pull --ff-only origin "$BRANCH" || true
else
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

if [ ! -d data/weather ]; then
  say "ВНИМАНИЕ: каталог data/ пуст"
  echo "Реальные данные (Sentinel-2, ERA5, Copernicus DEM) собирает пайплайн:"
  echo "  GitHub → Actions → workflow «fetch-data» → Run workflow"
  echo "После завершения: git pull && ./setup.sh"
fi

say "Собираю и запускаю"
PORT="$PORT" docker compose up -d --build

if [ -n "$DOMAIN" ]; then
  say "Настраиваю nginx и HTTPS для $DOMAIN"
  apt-get install -y nginx certbot python3-certbot-nginx
  cat >/etc/nginx/sites-available/akvachek <<NGINX
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
    }
}
NGINX
  ln -sf /etc/nginx/sites-available/akvachek /etc/nginx/sites-enabled/akvachek
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" || \
    echo "Сертификат не выпущен — сайт доступен по http://$DOMAIN"
fi

say "Готово"
echo "Проверка:  curl -s http://127.0.0.1:$PORT/api/meta | head -c 400"
echo "Логи:      docker compose -f $DIR/docker-compose.yml logs -f"
echo "Остановка: docker compose -f $DIR/docker-compose.yml down"
