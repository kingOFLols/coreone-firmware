# Build output

One folder per Prusa firmware release, written by the build workflow on the `main` branch.

| Folder | Contents |
|---|---|
| `<version>/coreone_<version>_L-limits.bbf` | the firmware to put on the USB stick |
| `<version>/coreone_L_limits.diff` | the exact source lines that were changed |
| `<version>/build.log` | the full build log |
| `<version>/status.txt` | result, run link and the file's SHA-256 |
