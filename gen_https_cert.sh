#!/usr/bin/env bash
# Generates a self-signed HTTPS certificate covering this machine's current
# LAN IP(s), so the app can serve over https:// - required for the browser
# Geolocation API to work (it's blocked on plain http://<lan-ip> addresses).
#
# The browser will show a security warning the first time you visit
# https://<pi-ip>:5050 (since it's self-signed, not from a public CA) -
# click through it once ("Advanced" -> "Proceed"); the browser then treats
# the connection as a secure origin from then on, and GPS location will work.
#
# Re-run this if your Pi's LAN IP changes (e.g. after a DHCP lease renewal
# assigns a different address) and restart smart-azan afterward.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | grep -v '^127\.')
SAN="DNS:localhost,IP:127.0.0.1"
for ip in $IPS; do
  SAN="$SAN,IP:$ip"
done

echo "==> Generating self-signed certificate covering: $SAN"
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout cert.key -out cert.pem -days 3650 \
  -subj "/CN=smart-azan.local" \
  -addext "subjectAltName=$SAN"

echo "==> Done. cert.pem / cert.key written to $PROJECT_DIR"
echo "    Restart smart-azan (sudo systemctl restart smart-azan) to pick it up."
echo "    Then visit https://<this-pi-ip>:5050 and accept the certificate warning once."
