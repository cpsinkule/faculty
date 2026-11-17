# Scanner folder (SCANNER-INTAKE "SCANNER FOLDER")

The home network scanners save PDFs into `/mnt/nvme/Teaching/scanner-folder/<scanner>/`, shared over SMB as
`\\<ml-box LAN address>\scans`. The Inbox worker retains each finished PDF as a scan and removes the folder's copy;
anything it refuses waits in `scanner-folder/not-accepted/` with a `.txt` note.

1. `sudo bash deploy/scanner-folder/setup.sh` (once). It installs Samba, creates the share-only login
   `faculty-scanner` and prints its password. The share answers on the home LAN only.
2. Give ml-box a fixed address on the router (a DHCP reservation), so the scanners' saved path keeps working.
3. On each scanner, add a network-folder (SMB) destination:
   - Path: `\\<ml-box address>\scans\brother` or `\\<ml-box address>\scans\epson`
   - User name `faculty-scanner`, the printed password, authentication NTLMv2 (or Auto)
   - File type PDF (multi-page, not Compact/High-compression PDF), Gray, 300 dpi, background removal off

Run `setup.sh` again to set a new password; it keeps the share.
