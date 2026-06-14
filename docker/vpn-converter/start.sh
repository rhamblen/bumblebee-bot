#!/bin/bash
set -e

# Find any .ovpn or .conf file in /vpn
VPN_CONFIG=$(find /vpn -name "*.ovpn" -o -name "vpn.conf" | head -1)

if [ -z "$VPN_CONFIG" ]; then
    echo "ERROR: No .ovpn or vpn.conf file found in /vpn"
    echo "Files present:"; ls -la /vpn/ 2>/dev/null
    exit 1
fi

echo "Using VPN config: $VPN_CONFIG"

# Save routing info BEFORE OpenVPN changes the routing table
DOCKER_GW=$(ip route show default | awk '/default/ {print $3; exit}')
echo "Docker gateway: $DOCKER_GW"
echo "Routes before VPN:"
ip route

OVPN_ARGS="--config $VPN_CONFIG"

# Build auth file from VPN_AUTH env var (format: "username;password")
AUTH_FILE="/tmp/vpn-auth.txt"
if [ -n "$VPN_AUTH" ]; then
    echo "$VPN_AUTH" | tr ';' '\n' > "$AUTH_FILE"
    OVPN_ARGS="$OVPN_ARGS --auth-user-pass $AUTH_FILE"
else
    for candidate in /vpn/auth.txt /vpn/credentials.conf /vpn/pass.txt; do
        if [ -f "$candidate" ]; then
            OVPN_ARGS="$OVPN_ARGS --auth-user-pass $candidate"
            echo "Using auth file: $candidate"
            break
        fi
    done
fi

echo "Starting OpenVPN..."
openvpn $OVPN_ARGS &
OVPN_PID=$!

# Wait for tun0 to come up (up to 30s)
for i in $(seq 1 30); do
    if ! kill -0 $OVPN_PID 2>/dev/null; then
        echo "ERROR: OpenVPN process exited unexpectedly"
        exit 1
    fi
    if ip link show tun0 > /dev/null 2>&1; then
        echo "VPN tunnel up (${i}s)"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: VPN did not connect within 30s"
        exit 1
    fi
    sleep 1
done

echo "Routes after VPN (before fix):"
ip route

# Restore LAN/Docker routes — NordVPN redirect-gateway def1 hijacks all traffic via tun0.
# We add more-specific routes for all RFC1918 ranges back via eth0 so that:
#   - Docker internal traffic (172.19.x.x) stays on eth0
#   - LAN traffic (192.168.x.x) stays on eth0 so port 5008 replies reach Windows
# Only 10.x.x.x is left on VPN (that's tun0's own subnet 10.100.x.x)
if [ -n "$DOCKER_GW" ]; then
    ip route add 172.16.0.0/12  via "$DOCKER_GW" dev eth0 2>/dev/null || true
    ip route add 192.168.0.0/16 via "$DOCKER_GW" dev eth0 2>/dev/null || true
fi

echo "Routes after fix:"
ip route

echo "Starting vpn-converter API on port 5008..."
exec uvicorn server:app --host 0.0.0.0 --port 5008
