#!/bin/sh
# CSAK bench ARP watchdog.
# After the board is hard power-cycled, macOS re-installs a proxy ARP entry
# binding the Pi's link-local IP to the Mac's OWN MAC, blackholing all IPv4
# to the board until `arp -d` is run. This script (run as root by launchd
# every 30 s) deletes that entry whenever it reappears. It only ever acts
# when the entry's MAC equals the local interface's MAC, so a genuine entry
# for the real Pi is never touched.
IP=169.254.52.213

LINE=$(arp -n "$IP" 2>/dev/null | grep -v 'no entry')
[ -z "$LINE" ] && exit 0
MAC=$(echo "$LINE" | awk '{print $4}')
IF=$(echo "$LINE" | awk '{for(i=1;i<NF;i++) if($i=="on") print $(i+1)}')
[ -z "$MAC" ] || [ -z "$IF" ] && exit 0
SELF=$(ifconfig "$IF" 2>/dev/null | awk '/ether/{print $2}')
if [ -n "$SELF" ] && [ "$MAC" = "$SELF" ]; then
    arp -d "$IP" >/dev/null 2>&1
    logger -t csak-arp-watchdog "removed bogus self-pointing ARP entry for $IP on $IF"
fi
