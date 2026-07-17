#!/usr/bin/env bash
# Privileged helper for UVProTermBT's one-click Winlink setup.
# Invoked via pkexec (runs as root). Kept small and strict — it runs as root,
# so a safe PATH, absolute-ish resolution, and argument validation matter.
#
#   winlink-helper.sh install
#   winlink-helper.sh attach <pty> <port> <callsign>
#   winlink-helper.sh detach <port>
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

die() { echo "winlink-helper: $*" >&2; exit 1; }

cmd="${1:-}"

case "$cmd" in
  install)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ax25-tools ax25-apps libax25
    ;;

  attach)
    pty="${2:-}"; port="${3:-}"; call="${4:-}"
    [ -n "$pty" ] && [ -n "$port" ] && [ -n "$call" ] || die "usage: attach <pty> <port> <callsign>"
    case "$pty" in /dev/pts/*) ;; *) die "refusing non-pty device: $pty" ;; esac
    [ -c "$pty" ] || die "not a character device: $pty"
    printf '%s' "$port" | grep -qE '^[A-Za-z0-9]+$'        || die "bad port name: $port"
    printf '%s' "$call" | grep -qE '^[A-Za-z0-9]+(-[0-9]+)?$' || die "bad callsign: $call"

    modprobe ax25 2>/dev/null || true
    mkdir -p /etc/ax25
    touch /etc/ax25/axports
    # keep exactly one axports line for this port, with the current callsign
    sed -i "/^[[:space:]]*${port}[[:space:]]/d" /etc/ax25/axports
    printf '%s\t%s\t1200\t255\t2\tUVProTermBT Winlink\n' "$port" "$call" >> /etc/ax25/axports

    kissattach "$pty" "$port"          # attaches + returns; iface persists
    kissparms -p "$port" -c 1 2>/dev/null || true
    echo "attached $call on $port ($pty)"
    ;;

  detach)
    port="${2:-}"
    [ -n "$port" ] || die "usage: detach <port>"
    printf '%s' "$port" | grep -qE '^[A-Za-z0-9]+$' || die "bad port name: $port"
    pkill -f "kissattach .*[[:space:]]${port}\$" 2>/dev/null || true
    for ifc in /sys/class/net/ax*; do
        [ -e "$ifc" ] || continue
        ip link set "$(basename "$ifc")" down 2>/dev/null || true
    done
    echo "detached $port"
    ;;

  *)
    die "unknown command: '$cmd' (install|attach|detach)"
    ;;
esac
