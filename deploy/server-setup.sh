#!/usr/bin/env bash
# AKVACHEK: one-shot deploy of the READY site onto a clean Ubuntu/Debian VPS.
# Does NOT touch any site code: it downloads prebuilt dist/ files and serves them with nginx.
# Safe to run twice. No credentials inside this file.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

REPO="Igorr112323/Ivan100"
REF="e4d27544b946cdf8c7030e00649b1968eb7a2486"
ROOT="/var/www/akvachek"
PUBLIC_IP="${PUBLIC_IP:-179.254.92.144}"

FILES="akvachek.html index.html questions.html qr.png vedomost.pdf akvachek_vedomost.pdf \
handout.pdf slides.pdf screen_1440x900.png screen_1280x720.png screen_1280x720_40.png screen_390x844.png"

MIRRORS=(
  "https://cdn.jsdelivr.net/gh/${REPO}@${REF}/dist"
  "https://raw.githubusercontent.com/${REPO}/${REF}/dist"
  "https://github.com/${REPO}/raw/${REF}/dist"
)

echo "=== 1/6 install nginx ==="
apt-get update -qq
apt-get install -y -qq -o Dpkg::Options::=--force-confold nginx curl ca-certificates >/dev/null
mkdir -p "$ROOT"

echo "=== 2/6 download ready site straight onto the server ==="
for f in $FILES; do
  got=0
  for base in "${MIRRORS[@]}"; do
    if curl -fsSL --connect-timeout 10 --max-time 60 "$base/$f" -o "$ROOT/$f.tmp" && [ -s "$ROOT/$f.tmp" ]; then
      mv "$ROOT/$f.tmp" "$ROOT/$f"
      got=1
      break
    fi
  done
  rm -f "$ROOT/$f.tmp"
  if [ "$got" = 1 ]; then
    echo "  ok   $f  ($(stat -c%s "$ROOT/$f") bytes)"
  else
    echo "  warn $f not downloaded (optional)"
  fi
done

# The only mandatory file: one self-contained HTML, 723577 bytes, zero external requests.
if [ ! -s "$ROOT/akvachek.html" ]; then
  echo "ERROR: akvachek.html was not downloaded. Check network from the server." >&2
  exit 1
fi
chown -R www-data:www-data "$ROOT" 2>/dev/null || true
chmod -R a+rX "$ROOT"

echo "=== 3/6 nginx config (NO per-IP connection limits) ==="
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
if [ -f /etc/nginx/sites-available/default ]; then
  cp -n /etc/nginx/sites-available/default /etc/nginx/sites-available/default.bak 2>/dev/null || true
fi
cat > /etc/nginx/sites-available/akvachek <<'NGINX'
server {
    listen 80;
    server_name _;
    root /var/www/akvachek;
    index akvachek.html;          # short URL: / opens the screen immediately

    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;
    client_max_body_size 8m;

    gzip on;
    gzip_comp_level 5;
    gzip_types text/plain text/css application/javascript application/json application/pdf image/svg+xml;

    access_log /var/log/nginx/akvachek.access.log;

    # query string (?limit=70) is untouched by try_files -> deep link works
    location / {
        try_files $uri $uri/ =404;
        add_header Cache-Control "no-cache" always;
    }
    location ~* \.(png|jpg|jpeg|pdf)$ {
        expires 1h;
        add_header Cache-Control "public" always;
        try_files $uri =404;
    }
    # Intentionally NO limit_conn / limit_req / auth_basic / deny / geo:
    # 10+ clients behind one NAT IP must all get 200.
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/akvachek /etc/nginx/sites-enabled/akvachek
nginx -t
systemctl enable --now nginx >/dev/null 2>&1 || service nginx restart
systemctl reload nginx 2>/dev/null || service nginx reload || true

echo "=== 4/6 open ports 22, 80, 443 ==="
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp  >/dev/null 2>&1 || true
  ufw allow 80/tcp  >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -I INPUT -p tcp --dport 80  -j ACCEPT 2>/dev/null || true
  iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
fi

echo "=== 5/6 self-check ==="
sleep 1
curl -sS -o /dev/null -w 'GET /                -> %{http_code}, %{size_download} bytes\n' http://127.0.0.1/
curl -sS -o /dev/null -w 'GET /akvachek.html   -> %{http_code}, %{size_download} bytes\n' http://127.0.0.1/akvachek.html
curl -sS -o /dev/null -w 'GET /?limit=70       -> %{http_code}\n' 'http://127.0.0.1/?limit=70'
echo "--- 20 parallel requests from ONE IP (all must be 200) ---"
seq 1 20 | xargs -P20 -I{} curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/akvachek.html | sort | uniq -c

echo "=== 6/6 DONE ==="
echo "Site:      http://${PUBLIC_IP}/"
echo "Deep link: http://${PUBLIC_IP}/?limit=70"
echo "Open it from a phone over MOBILE data (not Wi-Fi)."
echo "If unreachable from outside: open ports 80/443 in the HOSTING control panel (external firewall)."
