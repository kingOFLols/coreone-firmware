# CORE One firmware with CORE One L chamber limits

Builds Prusa's open-source firmware ([Prusa-Firmware-Buddy](https://github.com/prusa3d/Prusa-Firmware-Buddy), GPL-3.0)
for the CORE One, CORE One+ and CORE One+ (Gen 2) with ten chamber-limit values changed to the CORE One L's values.

| Limit | Stock CORE One | This build (= CORE One L) |
|---|---|---|
| Highest chamber target accepted | 55 °C | 60 °C |
| Fans forced to 100 % + warning | 60 °C | 65 °C |
| Print aborted, bed and nozzle heaters off | 65 °C | 68 °C |
| Normal fan control resumes below | 55 °C | 60 °C |
| Built-in PPS and PPA presets, chamber min / max / target | 45 / 65 / 55 °C | 55 / 70 / 60 °C |

- `build_coreone_L_limits.py` downloads the tagged source, applies and verifies the changes, and builds.
- `.github/workflows/build-coreone-firmware.yml` runs it on GitHub Actions (Ubuntu 24.04).
- The finished `.bbf`, the exact source changes and the build log land on the `build-output` branch
  and under **Artifacts** on the Actions run.

The firmware is unsigned: the printer only accepts it after the appendix seal on the xBuddy board is broken.
