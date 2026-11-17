#!/usr/bin/env bash
# SCANNER-INTAKE "SCANNER FOLDER": share Faculty's scanner folder with the home network scanners over SMB.
# Run once on ml-box with sudo:  sudo bash deploy/scanner-folder/setup.sh
# It installs Samba, makes a scanner-only login (no shell, no home, no other share), and shares only
# /mnt/nvme/Teaching/scanner-folder, to the home LAN only (not Tailscale, not the internet). It prints the password to
# type into both scanners. Running it again keeps the share and sets a new password.
set -euo pipefail

OWNER=${OWNER:-faculty}
FOLDER=${FOLDER:-/mnt/nvme/Teaching/scanner-folder}
LAN=${LAN:-192.0.2.0/24}
IFACE=${IFACE:-enp6s18}
LOGIN=faculty-scanner

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
[ -d "$FOLDER" ] || { echo "$FOLDER is missing; Faculty's checkout creates it." >&2; exit 1; }

command -v smbd >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q samba; }

id "$LOGIN" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$LOGIN"

[ -f /etc/samba/smb.conf.before-faculty ] || cp -p /etc/samba/smb.conf /etc/samba/smb.conf.before-faculty
cat > /etc/samba/smb.conf <<CONF
# Faculty scanner folder only (deploy/scanner-folder/setup.sh). The original is smb.conf.before-faculty.
[global]
   workgroup = WORKGROUP
   server string = ml-box
   server role = standalone server
   interfaces = lo $IFACE
   bind interfaces only = yes
   hosts allow = 127.0.0.1 $LAN
   hosts deny = ALL
   map to guest = never
   restrict anonymous = 2
   server min protocol = SMB2
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   log file = /var/log/samba/log.%m
   max log size = 1000

[scans]
   comment = Faculty scanner folder
   path = $FOLDER
   valid users = $LOGIN
   force user = $OWNER
   force group = $OWNER
   read only = no
   browseable = yes
   create mask = 0600
   directory mask = 0700
   follow symlinks = no
   wide links = no
CONF
testparm -s >/dev/null

# Ubuntu's AppArmor profile for smbd allows only the folders it is told about; without this every save is "access denied".
RULES=/etc/apparmor.d/local/usr.sbin.smbd
if [ -d /etc/apparmor.d/local ] && ! grep -qs "$FOLDER/\*\*" "$RULES"; then
  printf '  %s/ rw,\n  %s/** rwlk,\n' "$FOLDER" "$FOLDER" >> "$RULES"
fi
[ -f /etc/apparmor.d/usr.sbin.smbd ] && apparmor_parser -r /etc/apparmor.d/usr.sbin.smbd

PASSWORD=$(tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom | head -c 14 || true)
printf '%s\n%s\n' "$PASSWORD" "$PASSWORD" | smbpasswd -s -a "$LOGIN" >/dev/null
smbpasswd -e "$LOGIN" >/dev/null

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow from "$LAN" to any port 445 proto tcp comment "Faculty scanner folder"
fi
systemctl enable --now smbd >/dev/null 2>&1 || true
systemctl restart smbd
systemctl disable --now nmbd >/dev/null 2>&1 || true

echo
echo "Scanner folder shared."
echo "  Address:   \\\\$(ip -4 -o addr show "$IFACE" | awk '{print $4}' | cut -d/ -f1)\\scans"
echo "  User name: $LOGIN"
echo "  Password:  $PASSWORD"
