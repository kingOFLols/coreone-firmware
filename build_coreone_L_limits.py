#!/usr/bin/env python3
"""
Build Prusa CORE One firmware with the CORE One L's chamber limits.

Run it in Linux (Ubuntu 24.04 is what Prusa's own build container uses):

    python3 build_coreone_L_limits.py                    # newest final release it knows
    python3 build_coreone_L_limits.py --tag v8.0.3-beta  # a specific release

What it does
  1. installs the packages Prusa's own build container uses (asks for your password)
  2. downloads the firmware source for that release into ~/coreone-fw
  3. changes the CORE One's limits to the CORE One L's values and checks every change
  4. builds the firmware with Prusa's build script (downloads the compilers on the first run)
  5. copies the finished .bbf next to this script

Options
  --tag TAG      which release to build (see RELEASES below)
  --patch-only   stop after step 3 (patch and verify, no build)
  --src DIR      use an existing checkout of that release instead of downloading one
  --undo         put the three patched files back to stock and stop

Applies to the CORE One, CORE One+ and CORE One+ (Gen 2) without INDX (build preset "coreone").
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Releases this script knows. For each tag: the commit it must point to, the number of commits
# up to it in Prusa's public repo, and the pre-release label Prusa's own build of it carries.
# Official builds use the commit count as the build number, and the printer compares it with the
# "M115 U<version>+NNNNN" line in sliced G-code. The source is downloaded with its full commit
# list so the build counts the same number itself; it is also passed to the main build explicitly.
RELEASES = {
    "v8.0.2": ("b1043d68be188ad27ea72a14e160140e30e589f0", "17092", ""),
    "v8.0.3-beta": ("5f11b2176510624c9c190c8e46bee048e5218533", "17094", "-BETA"),
}
DEFAULT_TAG = "v8.0.2"  # newest final release
REPO = "https://github.com/prusa3d/Prusa-Firmware-Buddy.git"
PRESET = "coreone"
TAG = TAG_COMMIT = BUILD_NUMBER = PRERELEASE = None  # set by select_release()
BUILD_ARGS = []


def select_release(tag):
    """Point the script at one of the known releases."""
    global TAG, TAG_COMMIT, BUILD_NUMBER, PRERELEASE, BUILD_ARGS
    TAG = tag
    TAG_COMMIT, BUILD_NUMBER, PRERELEASE = RELEASES[tag]
    # Settings Prusa's own release builds use (utils/holly/build-pr.jenkins). Without the
    # DEVELOPMENT_ITEMS one a local build is a developer build: green developer menus, and
    # metrics reporting switched on by default.
    BUILD_ARGS = ["--preset", PRESET, "--bootloader", "yes", "--build-type", "release",
                  "--cmake-def", "BUILD_NUMBER:STRING=" + BUILD_NUMBER,
                  "--cmake-def", "DEVELOPMENT_ITEMS_ENABLED:BOOL=NO"]
    if PRERELEASE:
        # label a build of a pre-release the way Prusa labels its own (8.0.3-BETA+17094), plus .LOCAL
        BUILD_ARGS += ["--version-suffix=%s+%s.LOCAL" % (PRERELEASE, BUILD_NUMBER),
                       "--version-suffix-short=%s+%s" % (PRERELEASE, BUILD_NUMBER)]


def version_label():
    """What this build calls itself on the printer, e.g. 8.0.2+17092.LOCAL or 8.0.3-BETA+17094.LOCAL."""
    base = re.match(r"v(\d+\.\d+\.\d+)", TAG).group(1)
    return "%s%s+%s.LOCAL" % (base, PRERELEASE, BUILD_NUMBER)
# Same list as utils/holly/Dockerfile (Prusa's build container, Ubuntu 24.04)
APT_PACKAGES = ["git", "python3", "python3-pip", "python3-venv", "python3-requests", "libjpeg-dev", "libusb-1.0-0", "gettext"]

CHAMBER_CPP = "src/feature/chamber/chamber.cpp"
COOLING_HPP = "src/feature/xbuddy_extension/cooling.hpp"
PRESETS_CPP = "src/common/filament_presets.cpp"

# constant -> (file, stock CORE One value, CORE One L value, what it does)
BLOCK_EDITS = [
    ("chamber_maxtemp", CHAMBER_CPP, "60", "65", "highest accepted chamber target = this minus 5"),
    ("overheating_temp", COOLING_HPP, "60.0f", "65.0f", "chamber fans forced to 100 % + warning"),
    ("critical_temp", COOLING_HPP, "65.0f", "68.0f", "print aborted, bed and nozzle heaters off"),
    ("recovery_temp", COOLING_HPP, "55.0f", "60.0f", "normal fan control resumes below this"),
]
# built-in PPS and PPA filament presets: field -> (stock CORE One value, CORE One L value)
PRESET_EDITS = {
    "chamber_min_temperature": ("45", "55"),
    "chamber_max_temperature": ("65", "70"),
    "chamber_target_temperature": ("55", "60"),
}


def die(msg):
    print("\nSTOPPED: " + msg, file=sys.stderr)
    sys.exit(1)


def run(cmd, cwd=None, check=True, capture=False):
    if not capture:
        print("  $ " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None,
                            universal_newlines=True)
    if check and result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout, file=sys.stderr)
        die("command failed: " + " ".join(str(c) for c in cmd))
    return result


# ---------------------------------------------------------------------------------------------
# patching
# ---------------------------------------------------------------------------------------------
def read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:  # newline='' keeps LF / CRLF as is
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def model_blocks(text, fname):
    """Return ((start, end) of the '#if COREONE' block, (start, end) of the '#elif COREONEL' block)."""
    starts = list(re.finditer(r"^#if PRINTER_IS_PRUSA_COREONE\(\)[ \t]*\r?$", text, re.M))
    if len(starts) != 1:
        die("%s: expected exactly one '#if PRINTER_IS_PRUSA_COREONE()' block, found %d. "
            "This is not the %s source the patch was written for." % (fname, len(starts), TAG))
    c1_start = starts[0].end()
    l_dir = re.compile(r"^#elif PRINTER_IS_PRUSA_COREONEL\(\)[ \t]*\r?$", re.M).search(text, c1_start)
    if not l_dir:
        die("%s: no '#elif PRINTER_IS_PRUSA_COREONEL()' after the CORE One block." % fname)
    any_dir = re.compile(r"^#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b", re.M)
    # nothing but plain code may sit between '#if COREONE' and '#elif COREONEL'
    nested = any_dir.search(text, c1_start, l_dir.start())
    if nested:
        die("%s: unexpected preprocessor line inside the CORE One block." % fname)
    l_end = any_dir.search(text, l_dir.end())
    if not l_end:
        die("%s: CORE One L block is not closed." % fname)
    return (c1_start, l_dir.start()), (l_dir.end(), l_end.start())


def const_in(text, span, name, fname):
    pat = re.compile(r"(\b" + re.escape(name) + r"[ \t]*=[ \t]*)([0-9]+(?:\.[0-9]+)?f?)([ \t]*;)")
    found = list(pat.finditer(text, span[0], span[1]))
    if len(found) != 1:
        die("%s: expected one '%s = ...;' in the block, found %d." % (fname, name, len(found)))
    return found[0]


def patch_tree(src, undo=False):
    """Apply (or undo) the edits. Returns a list of report rows. Stops on anything unexpected."""
    rows = []
    texts = {f: read(src / f) for f in (CHAMBER_CPP, COOLING_HPP, PRESETS_CPP)}

    for name, fname, stock, lval, what in BLOCK_EDITS:
        text = texts[fname]
        c1, l = model_blocks(text, fname)
        l_now = const_in(text, l, name, fname).group(2)
        if l_now != lval:
            die("%s: the CORE One L's own %s is %s, not %s. Prusa changed the L's limits in this "
                "source, so this script's numbers are out of date." % (fname, name, l_now, lval))
        m = const_in(text, c1, name, fname)
        now = m.group(2)
        want = stock if undo else lval
        if now not in (stock, lval):
            die("%s: CORE One %s is %s; expected %s (stock) or %s (patched)." % (fname, name, now, stock, lval))
        if now != want:
            texts[fname] = text[:m.start(2)] + want + text[m.end(2):]
        rows.append((name, what, stock, lval, now, want))

    text = texts[PRESETS_CPP]
    pat = re.compile(r"(\.(chamber_(?:min|max|target)_temperature)[ \t]*=[ \t]*PRINTER_IS_PRUSA_COREONEL\(\)[ \t]*\?[ \t]*)"
                     r"([0-9]+)([ \t]*:[ \t]*)([0-9]+)([ \t]*,)")
    found = list(pat.finditer(text))
    if len(found) != 6:
        die("%s: expected 6 per-model chamber values (PPS and PPA), found %d." % (PRESETS_CPP, len(found)))
    out, pos = [], 0
    for m in found:
        field, l_now, now = m.group(2), m.group(3), m.group(5)
        stock, lval = PRESET_EDITS[field]
        if l_now != lval:
            die("%s: the CORE One L's own %s is %s, not %s." % (PRESETS_CPP, field, l_now, lval))
        if now not in (stock, lval):
            die("%s: CORE One %s is %s; expected %s or %s." % (PRESETS_CPP, field, now, stock, lval))
        want = stock if undo else lval
        out.append(text[pos:m.start(5)] + want)
        pos = m.end(5)
        preset = "PPS" if text.rfind('.name = "PPS"', 0, m.start()) > text.rfind('.name = "PPA"', 0, m.start()) else "PPA"
        rows.append(("%s %s" % (preset, field.replace("chamber_", "").replace("_temperature", "")),
                     "built-in %s filament preset" % preset, stock, lval, now, want))
    texts[PRESETS_CPP] = "".join(out) + text[pos:]

    for fname, text in texts.items():
        if text != read(src / fname):
            write(src / fname, text)
    return rows


def verify_tree(src, undo=False):
    """Re-read the files from disk and confirm the CORE One side now equals the L side (or stock)."""
    problems = []
    for name, fname, stock, lval, _ in BLOCK_EDITS:
        text = read(src / fname)
        c1, l = model_blocks(text, fname)
        now, l_now = const_in(text, c1, name, fname).group(2), const_in(text, l, name, fname).group(2)
        want = stock if undo else l_now
        if now != want:
            problems.append("%s %s = %s (want %s)" % (fname, name, now, want))
    text = read(src / PRESETS_CPP)
    for m in re.finditer(r"\.(chamber_(?:min|max|target)_temperature)[ \t]*=[ \t]*PRINTER_IS_PRUSA_COREONEL\(\)"
                         r"[ \t]*\?[ \t]*([0-9]+)[ \t]*:[ \t]*([0-9]+)", text):
        want = PRESET_EDITS[m.group(1)][0] if undo else m.group(2)
        if m.group(3) != want:
            problems.append("%s %s = %s (want %s)" % (PRESETS_CPP, m.group(1), m.group(3), want))
    if problems:
        die("verification failed:\n  " + "\n  ".join(problems))


def print_rows(rows):
    print("\n  %-24s %-8s %-8s %-10s %s" % ("limit", "stock", "L value", "now", "meaning"))
    print("  " + "-" * 96)
    for name, what, stock, lval, before, after in rows:
        note = "" if before != after else "  (was already set)"
        print("  %-24s %-8s %-8s %-10s %s%s" % (name, stock, lval, after, what, note))
    print()


# ---------------------------------------------------------------------------------------------
# environment, source, build
# ---------------------------------------------------------------------------------------------
def ensure_packages():
    if not shutil.which("apt-get") or not shutil.which("dpkg"):
        missing_git = not shutil.which("git")
        print("  Not a Debian/Ubuntu system, so nothing was installed. Prusa's build container uses:\n   "
              + " ".join(APT_PACKAGES))
        if missing_git:
            die("git is not installed.")
        return
    missing = [p for p in APT_PACKAGES
               if subprocess.run(["dpkg", "-s", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0]
    if not missing:
        print("  all needed packages are installed")
        return
    print("  installing: " + " ".join(missing))
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    run(sudo + ["apt-get", "update", "-qq"])
    run(sudo + ["apt-get", "install", "-y"] + missing)


def build_python():
    """Prusa's pinned Python packages (numpy 1.26.4) only have ready-made builds for Python <= 3.12."""
    for exe in (sys.executable, "python3.12", "python3.11"):
        path = exe if os.path.isabs(exe) else shutil.which(exe)
        if not path:
            continue
        r = subprocess.run([path, "-c", "import sys, venv, ensurepip; print('%d.%d' % sys.version_info[:2])"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        if r.returncode == 0 and r.stdout.strip() in ("3.11", "3.12"):
            return path, r.stdout.strip()
    die("the build needs Python 3.12 (or 3.11) with the venv module; this system's python3 is %d.%d.\n"
        "On Windows, use the Ubuntu 24.04 WSL image (it ships Python 3.12):  wsl --install -d Ubuntu-24.04"
        % sys.version_info[:2])


def get_source(src):
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        # --filter=tree:0 downloads the full list of commits (needed for the build number) but only
        # the files of this one version, so it stays about as small as a shallow clone
        run(["git", "-c", "advice.detachedHead=false", "clone", "--filter=tree:0", "--single-branch",
             "--branch", TAG, REPO, src])
    if not (src / "utils" / "build.py").exists():
        die("%s is not a Prusa-Firmware-Buddy checkout." % src)
    head = run(["git", "rev-parse", "HEAD"], cwd=src, capture=True).stdout.strip()
    if head != TAG_COMMIT:
        die("%s is at commit %s, not at %s (%s).\nDelete that folder (or pass --src to a %s checkout) and run again."
            % (src, head[:12], TAG, TAG_COMMIT[:12], TAG))
    print("  source: %s at %s" % (src, TAG))
    count = run(["git", "rev-list", "--count", "HEAD"], cwd=src, capture=True).stdout.strip()
    if count == BUILD_NUMBER:
        print("  commit count: %s (the build number official %s builds carry)" % (count, TAG))
    else:
        print("  NOTE: this checkout counts %s commits, not %s (a shallow clone?). The main firmware still gets\n"
              "  build number %s, but the two secondary board firmwares will be labelled +%s. To avoid that,\n"
              "  delete %s and run again." % (count, BUILD_NUMBER, BUILD_NUMBER, count, src))


def windows_path(p):
    m = re.match(r"^/mnt/([a-z])/(.*)$", str(p))
    return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\")) if m else str(p)


def main():
    ap = argparse.ArgumentParser(description="Build CORE One firmware with the CORE One L chamber limits.")
    ap.add_argument("--tag", choices=sorted(RELEASES), default=DEFAULT_TAG,
                    help="release to build (default: %s)" % DEFAULT_TAG)
    ap.add_argument("--patch-only", action="store_true", help="patch and verify, do not build")
    ap.add_argument("--undo", action="store_true", help="restore the stock values and stop")
    ap.add_argument("--src", type=Path, default=None,
                    help="firmware source folder (default: ~/coreone-fw/Prusa-Firmware-Buddy-<tag>)")
    args = ap.parse_args()
    select_release(args.tag)
    src = (args.src or Path.home() / "coreone-fw" / ("Prusa-Firmware-Buddy-" + TAG)).expanduser().resolve()
    out_dir = Path(__file__).resolve().parent

    if sys.platform.startswith("win"):
        die("this has to run in Linux. Firmware %s builds the Wi-Fi module firmware with bash scripts, so "
            "Prusa's build does not run in Windows Command Prompt or PowerShell.\n"
            "Install WSL once (PowerShell as administrator):  wsl --install -d Ubuntu-24.04\n"
            "then run this script inside the Ubuntu window:      python3 build_coreone_L_limits.py" % TAG)
    if str(src).startswith("/mnt/"):
        print("NOTE: %s is on the Windows drive; building there is slow. The default (~/coreone-fw) is faster." % src)

    print("[1/5] Packages")
    if args.patch_only or args.undo:
        if not shutil.which("git"):
            die("git is not installed.")
        print("  skipped (not building)")
    else:
        ensure_packages()
        py, pyver = build_python()
        print("  build Python: %s (%s)" % (py, pyver))
        free_gb = shutil.disk_usage(str(src.parent if src.parent.exists() else Path.home())).free / 1e9
        if free_gb < 10:
            print("  NOTE: only %.0f GB free here; the compilers plus the build take several GB." % free_gb)

    print("[2/5] Firmware source")
    get_source(src)

    print("[3/5] %s" % ("Restoring stock limits" if args.undo else "Setting the CORE One limits to the CORE One L values"))
    rows = patch_tree(src, undo=args.undo)
    verify_tree(src, undo=args.undo)
    print_rows(rows)
    diff = run(["git", "diff", "--", CHAMBER_CPP, COOLING_HPP, PRESETS_CPP], cwd=src, capture=True).stdout
    other = [l for l in run(["git", "status", "--porcelain"], cwd=src, capture=True).stdout.splitlines()
             if l[:2].strip() == "M" and l[3:] not in (CHAMBER_CPP, COOLING_HPP, PRESETS_CPP)]
    if other:
        print("  NOTE: other source files in this checkout are modified too:\n   " + "\n   ".join(other))
    if args.undo:
        if diff.strip():
            die("the three files still differ from stock; run 'git diff' in %s." % src)
        print("  verified: the three files are back to stock %s." % TAG)
        return
    print("  verified: all %d values now equal the CORE One L's values in the same files." % len(rows))
    try:
        (out_dir / "coreone_L_limits.diff").write_text(diff, encoding="utf-8")
        print("  change list saved as %s" % (out_dir / "coreone_L_limits.diff"))
    except OSError:
        pass
    if args.patch_only:
        print("\nPatched. To build yourself:\n  cd %s\n  python3 utils/build.py %s" % (src, " ".join(BUILD_ARGS)))
        return

    print("[4/5] Building %s (the first run downloads the compilers; leave it running)" % version_label())
    products = src / "build" / "products"
    pattern = "%s_release_boot*.bbf" % PRESET  # a pre-release label becomes part of the file name
    for old in products.glob(pattern):
        old.unlink()
    env_hint = "If it stops with a download error, check the internet connection and run this script again; it resumes."
    env = dict(os.environ)
    if env.get("WSL_DISTRO_NAME") or "microsoft" in os.uname().release.lower():
        # WSL appends the Windows PATH (folders with spaces) to the Linux PATH; the build does not need it
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and not p.startswith("/mnt/"))
    r = subprocess.run([py, "utils/build.py"] + BUILD_ARGS, cwd=str(src), env=env)
    built = sorted(products.glob(pattern))
    if r.returncode != 0 or len(built) != 1:
        die("the build did not finish (exit code %s, %d firmware file(s) produced).\n%s\n"
            "Copy the last ~30 lines above if you want help with it." % (r.returncode, len(built), env_hint))
    product = built[0]

    print("[5/5] Done")
    name = "coreone_%s_L-limits.bbf" % TAG.lstrip("v")
    dest = out_dir / name
    try:
        shutil.copyfile(str(product), str(dest))
    except OSError:
        dest = product
    print("\n  Firmware: %s" % dest)
    if str(dest).startswith("/mnt/"):
        print("  In Windows: %s" % windows_path(dest))
    print("  It is unsigned, so the printer only accepts it after the appendix seal on the xBuddy board is broken.\n"
          "  Copy it to a FAT32 USB stick with no other .bbf files, press reset on the printer,\n"
          "  press the knob when the Prusa logo shows, and choose FLASH.\n"
          "  After flashing, the start-up screen reads \"Firmware %s\"." % version_label())


if __name__ == "__main__":
    main()
