#!/usr/bin/env bash
# DDE tools environment setup
# Creates a Python venv, installs pip dependencies, and downloads
# non-pip binaries used by dde agent skills.
#
# Usage:
#   cd tools && ./install.sh              # fresh install
#   cd tools && ./install.sh --update     # update existing venv
#   cd tools && ./install.sh --core-only  # CLI deps only, skip the science stack
#   cd tools && ./install.sh --binaries-only  # binaries + stamp, leave pip alone
#   DDE_VENV=/some/shared/.venv ./install.sh   # install elsewhere
#
# The venv defaults to ${DDE_TOOLS_HOME}/.venv (which itself defaults
# to /scion-volumes/tools/.venv) but can live anywhere, so a shared
# volume can host one environment that several agents run out of without
# each of them building their own copy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_HOME_DIR="${DDE_TOOLS_HOME:-/scion-volumes/tools}"
VENV_DIR="${DDE_VENV:-${TOOLS_HOME_DIR}/.venv}"
BIN_DIR="${DDE_BIN:-${TOOLS_HOME_DIR}/bin}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> WARNING:\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------

UPDATE_ONLY=false
CORE_ONLY=false
BINARIES_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --update) UPDATE_ONLY=true ;;
        --core-only) CORE_ONLY=true ;;
        --binaries-only) BINARIES_ONLY=true; UPDATE_ONLY=true ;;
        --help|-h)
            echo "Usage: $0 [--update] [--core-only]"
            echo "  --update      Skip venv creation, just update pip packages and binaries"
            echo "  --core-only   Install requirements.txt only; skip requirements-science.txt"
            echo "  --binaries-only  Install bin/ tools and re-stamp; touch no Python package"
            echo ""
            echo "Environment:"
            echo "  DDE_VENV        venv location (default: \${DDE_TOOLS_HOME}/.venv)"
            echo "  DDE_BIN         binary directory (default: \${DDE_TOOLS_HOME}/bin)"
            echo "  DDE_TOOLS_HOME  tools home / stamp dir (default: /scion-volumes/tools)"
            exit 0
            ;;
        *) err "Unknown argument: $arg" ;;
    esac
done

# ---------------------------------------------------------------------------
# Python venv
# ---------------------------------------------------------------------------

if [ "$UPDATE_ONLY" = false ]; then
    log "Creating Python virtual environment at ${VENV_DIR}"
    python3 -m venv "$VENV_DIR"
fi

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    err "Virtual environment not found at ${VENV_DIR}. Run without --update first."
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# --binaries-only exists because the two halves of this environment
# change at different rates and for different reasons. Adding a compiled
# tool should not carry the risk of an unpinned dependency resolving one
# minor version forward on the same afternoon — that turns one
# explainable partition into two entangled ones, and the pip half is the
# one nobody meant to change.
if [ "$BINARIES_ONLY" = false ]; then
    log "Upgrading pip"
    pip install --upgrade pip --quiet
fi

# The two requirement files are installed in separate transactions on
# purpose. pip resolves and installs a file atomically, so when prody
# failed to compile it took click, numpy and zstandard down with it and
# left an empty venv — an unusable CLI as the reported consequence of an
# optional dependency. Core first, and its failure is fatal; the science
# stack second, and its failure is loud but survivable.

if [ "$BINARIES_ONLY" = false ]; then
    log "Installing core CLI dependencies from requirements.txt"
    pip install -r "${SCRIPT_DIR}/requirements.txt" --quiet \
        || err "Core dependencies failed to install; the CLI will not run."
fi

SCIENCE_STATUS="skipped"
if [ "$CORE_ONLY" = false ] && [ "$BINARIES_ONLY" = false ]; then
    log "Installing science stack from requirements-science.txt"
    if pip install -r "${SCRIPT_DIR}/requirements-science.txt" --quiet; then
        SCIENCE_STATUS="installed"
    else
        SCIENCE_STATUS="failed"
        warn "The science stack did not install. The dde CLI itself is"
        warn "unaffected — it imports none of those packages — but specialist"
        warn "skills that use rdkit, biopython, prody, scipy, pandas or"
        warn "matplotlib will raise DependencyError until this is resolved."
        warn "Most often this is a missing toolchain:"
        warn "  apt-get install -y build-essential python3-dev"
    fi
fi

# ---------------------------------------------------------------------------
# Non-pip binaries
# ---------------------------------------------------------------------------

mkdir -p "$BIN_DIR"

# Every binary here ends up hashed into ENV_VERSION, so each one is
# declared with the version it claims to be and installed by a function
# that is safe to re-run. Re-running must be free: an environment you
# cannot re-provision without churning its identity is an environment
# nobody will re-provision.

# --- AutoDock Vina ---
# Molecular docking engine used by structural-biologist and
# computational-chemist skills for binding pose prediction.
VINA_VERSION="1.2.5"
VINA_URL="https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v${VINA_VERSION}/vina_1.2.5_linux_x86_64"

install_vina() {
    local target="${BIN_DIR}/vina"
    if [ -x "$target" ]; then
        log "AutoDock Vina already installed at ${target}"
        return 0
    fi
    log "Downloading AutoDock Vina ${VINA_VERSION}"
    if curl -fsSL -o "$target" "$VINA_URL" 2>/dev/null; then
        chmod +x "$target"
        log "AutoDock Vina installed at ${target}"
    else
        warn "Could not download AutoDock Vina (network may be unavailable). Skipping."
        rm -f "$target"
        return 1
    fi
}

# --- fpocket ---
# Ligand-binding-site detection. Not packaged for Debian, so it is built
# from a pinned source tarball.
#
# Built STATIC on purpose. A normally linked fpocket runs in the
# container that built it and fails in every other one, because the base
# image has no libstdc++ guarantee and the failure surfaces as a missing
# .so at the moment a specialist needs an answer. Static costs 1.7 MB and
# removes the whole class.
FPOCKET_VERSION="4.2.2"
FPOCKET_URL="https://github.com/Discngine/fpocket/archive/refs/tags/${FPOCKET_VERSION}.tar.gz"
FPOCKET_SHA256="4042125e7243e03465200bee787e55a54c16c1a10908718af75275c46bfafaad"

install_fpocket() {
    local target="${BIN_DIR}/fpocket"
    if [ -x "$target" ] && "$target" 2>&1 | grep -q "fpocket"; then
        log "fpocket already installed at ${target}"
        return 0
    fi
    if ! command -v gcc &>/dev/null || ! command -v make &>/dev/null; then
        warn "fpocket needs gcc and make to build from source; skipping."
        warn "  apt-get install -y build-essential"
        return 1
    fi

    local work
    work="$(mktemp -d)"
    log "Building fpocket ${FPOCKET_VERSION} (static) in ${work}"

    if ! curl -fsSL -o "${work}/src.tar.gz" "$FPOCKET_URL"; then
        warn "Could not download fpocket source. Skipping."
        rm -rf "$work"
        return 1
    fi

    # The pin is checked, not just written down. An unverified pinned URL
    # records a version number and guarantees nothing about the bytes.
    local got
    got="$(sha256sum "${work}/src.tar.gz" | cut -d' ' -f1)"
    if [ "$got" != "$FPOCKET_SHA256" ]; then
        warn "fpocket source checksum mismatch; refusing to build."
        warn "  expected ${FPOCKET_SHA256}"
        warn "  got      ${got}"
        rm -rf "$work"
        return 1
    fi

    tar -xzf "${work}/src.tar.gz" -C "$work"
    local src="${work}/fpocket-${FPOCKET_VERSION}"

    # -pg and -g are dropped from upstream's default CFLAGS: profiling
    # makes the binary write gmon.out into whatever directory an agent
    # happened to run it from, which would litter a program's raw/ tree
    # with build artefacts of ours.
    local cflags="-W -Wextra -Wwrite-strings -Wstrict-prototypes -DM_OS_LINUX"
    cflags="${cflags} -DMNO_MEM_DEBUG -O2 -std=gnu99 -Iplugins/include"
    cflags="${cflags} -Iplugins/LINUXAMD64/molfile"
    local lflags="-static -lm -Lplugins/LINUXAMD64/molfile"
    lflags="${lflags} plugins/LINUXAMD64/molfile/libmolfile_plugin.a -lstdc++"

    # Two steps, and the order is not decoration. The vendored qhull has
    # its own makefile with its own include paths; `make bin/fpocket`
    # alone makes the top-level makefile compile those sources with the
    # wrong flags and fail on a missing libqhull.h. Build qhull with its
    # own rules first, and without passing CFLAGS down — a command-line
    # variable propagates into sub-makes and breaks the same build a
    # second way.
    if ! (cd "$src" && make qhull > "${work}/qhull.log" 2>&1); then
        warn "fpocket: vendored qhull failed to build; see ${work}/qhull.log (kept)."
        return 1
    fi
    if ! (cd "$src" && make bin/fpocket CFLAGS="$cflags" LFLAGS="$lflags" \
            > "${work}/build.log" 2>&1); then
        warn "fpocket build failed; see ${work}/build.log (kept)."
        return 1
    fi

    # Two acceptance checks before this binary is allowed near the shared
    # volume, because "it built" is not the claim being made.
    #
    # 1. Static. `ldd` on a static binary says it is not dynamic; if the
    #    link went dynamic anyway the binary works here and breaks there,
    #    which is the exact failure the static link was for.
    if ldd "${src}/bin/fpocket" 2>&1 | grep -qv "not a dynamic executable"; then
        warn "fpocket linked against shared libraries; refusing to install."
        ldd "${src}/bin/fpocket" 2>&1 | sed 's/^/    /' >&2
        rm -rf "$work"
        return 1
    fi

    # 2. Correct. Upstream ships reference outputs; ours must reproduce
    #    them line for line, excluding Volume — fpocket estimates pocket
    #    volume by Monte Carlo seeded from the clock, so volume moves
    #    between runs and upstream's own test suite excludes it too.
    local ref="${src}/tests/reference_output/1UYD_out/1UYD_info.txt"
    if [ -f "$ref" ]; then
        (cd "$src" && env -i ./bin/fpocket -f data/sample/1UYD.pdb >/dev/null 2>&1) || true
        local mine="${src}/data/sample/1UYD_out/1UYD_info.txt"
        if [ ! -f "$mine" ]; then
            warn "fpocket built but produced no output on the reference input; refusing."
            rm -rf "$work"
            return 1
        fi
        if ! diff <(grep -v Volume "$mine") <(grep -v Volume "$ref") >/dev/null; then
            warn "fpocket output does not match upstream's reference; refusing to install."
            diff <(grep -v Volume "$mine") <(grep -v Volume "$ref") | head -20 >&2
            rm -rf "$work"
            return 1
        fi
        log "fpocket reproduces upstream reference output (1UYD, volume excluded)"
    else
        warn "fpocket reference output missing from the tarball; installed unverified."
    fi

    install -m 0755 "${src}/bin/fpocket" "$target"
    rm -rf "$work"
    log "fpocket ${FPOCKET_VERSION} installed at ${target} (static)"
}

# --- hypex ---
# Hypothesis-explorer datastore lifecycle and integrity CLI, used by
# the hypex sub-team for tournament management (init-run, add-hypothesis,
# add-match, set-status, validate, etc.).
#
# PLACEHOLDER URLs — the scion-frontiers/hypex repo has not published
# GitHub releases yet.  Replace with real release URLs once available.
HYPEX_VERSION="1.0.0"
HYPEX_URL="PLACEHOLDER://github.com/scion-frontiers/hypex/releases/download/v${HYPEX_VERSION}/hypex_${HYPEX_VERSION}_linux_amd64"
HYPEX_SHA256="PLACEHOLDER"

install_hypex() {
    local target="${BIN_DIR}/hypex"
    if [ -x "$target" ]; then
        log "hypex already installed at ${target}"
        return 0
    fi
    if [ "$HYPEX_SHA256" = "PLACEHOLDER" ] || [[ "$HYPEX_URL" == PLACEHOLDER://* ]]; then
        log "Binary hypex has no upstream release. Skipping."
        return 0
    fi
    log "Downloading hypex ${HYPEX_VERSION}"
    if curl -fsSL -o "$target" "$HYPEX_URL" 2>/dev/null; then
        chmod +x "$target"
        local got
        got="$(sha256sum "$target" | cut -d' ' -f1)"
        if [ "$got" != "$HYPEX_SHA256" ]; then
            warn "hypex checksum mismatch; refusing to install."
            rm -f "$target"
            return 1
        fi
        log "hypex installed at ${target}"
    else
        warn "Could not download hypex (network may be unavailable). Skipping."
        rm -f "$target"
        return 1
    fi
}

# --- elo ---
# ELO rating engine for hypothesis tournaments.  Computes pairwise
# ratings from match ledgers and produces per-epoch standings.
HYPEX_ELO_VERSION="1.0.0"
HYPEX_ELO_URL="PLACEHOLDER://github.com/scion-frontiers/hypex/releases/download/v${HYPEX_ELO_VERSION}/elo_${HYPEX_ELO_VERSION}_linux_amd64"
HYPEX_ELO_SHA256="PLACEHOLDER"

install_elo() {
    local target="${BIN_DIR}/elo"
    if [ -x "$target" ]; then
        log "elo already installed at ${target}"
        return 0
    fi
    if [ "$HYPEX_ELO_SHA256" = "PLACEHOLDER" ] || [[ "$HYPEX_ELO_URL" == PLACEHOLDER://* ]]; then
        log "Binary elo has no upstream release. Skipping."
        return 0
    fi
    log "Downloading elo ${HYPEX_ELO_VERSION}"
    if curl -fsSL -o "$target" "$HYPEX_ELO_URL" 2>/dev/null; then
        chmod +x "$target"
        local got
        got="$(sha256sum "$target" | cut -d' ' -f1)"
        if [ "$got" != "$HYPEX_ELO_SHA256" ]; then
            warn "elo checksum mismatch; refusing to install."
            rm -f "$target"
            return 1
        fi
        log "elo installed at ${target}"
    else
        warn "Could not download elo (network may be unavailable). Skipping."
        rm -f "$target"
        return 1
    fi
}

# --- prox ---
# Proximity / similarity tool for hypothesis clustering.  Computes
# TF-IDF similarity between hypotheses and produces cluster
# assignments that feed ELO pairing and merge recommendations.
HYPEX_PROX_VERSION="0.1.0"
HYPEX_PROX_URL="PLACEHOLDER://github.com/scion-frontiers/hypex/releases/download/v${HYPEX_PROX_VERSION}/prox_${HYPEX_PROX_VERSION}_linux_amd64"
HYPEX_PROX_SHA256="PLACEHOLDER"

install_prox() {
    local target="${BIN_DIR}/prox"
    if [ -x "$target" ]; then
        log "prox already installed at ${target}"
        return 0
    fi
    if [ "$HYPEX_PROX_SHA256" = "PLACEHOLDER" ] || [[ "$HYPEX_PROX_URL" == PLACEHOLDER://* ]]; then
        log "Binary prox has no upstream release. Skipping."
        return 0
    fi
    log "Downloading prox ${HYPEX_PROX_VERSION}"
    if curl -fsSL -o "$target" "$HYPEX_PROX_URL" 2>/dev/null; then
        chmod +x "$target"
        local got
        got="$(sha256sum "$target" | cut -d' ' -f1)"
        if [ "$got" != "$HYPEX_PROX_SHA256" ]; then
            warn "prox checksum mismatch; refusing to install."
            rm -f "$target"
            return 1
        fi
        log "prox installed at ${target}"
    else
        warn "Could not download prox (network may be unavailable). Skipping."
        rm -f "$target"
        return 1
    fi
}

# --- rate4site ---
# Evolutionary conservation scoring engine used by conservation analysis.
# Built from source with g++; no cmake, no autotools, no external
# libraries beyond libc/libstdc++/libm.
RATE4SITE_SHA="fa13d64beaaa544fd977566f16eeb8ec44f80458"

install_rate4site() {
    local target="${BIN_DIR}/rate4site"
    if [ -x "$target" ]; then
        log "rate4site already installed at ${target}"
        return 0
    fi
    if ! command -v g++ &>/dev/null; then
        warn "rate4site needs g++ to build from source; skipping."
        warn "  apt-get install -y build-essential"
        return 1
    fi

    local tmpdir
    tmpdir="$(mktemp -d)"
    log "Installing rate4site from source (pinned ${RATE4SITE_SHA})..."
    if ! git clone https://github.com/barakav/r4s_for_collab.git "$tmpdir/r4s" 2>/dev/null; then
        warn "Could not clone rate4site source (network may be unavailable). Skipping."
        rm -rf "$tmpdir"
        return 1
    fi
    if ! (cd "$tmpdir/r4s" && git checkout "$RATE4SITE_SHA" 2>/dev/null); then
        warn "Could not checkout pinned rate4site commit ${RATE4SITE_SHA}. Skipping."
        rm -rf "$tmpdir"
        return 1
    fi
    if ! (cd "$tmpdir/r4s" && make) 2>/dev/null; then
        warn "rate4site build failed."
        rm -rf "$tmpdir"
        return 1
    fi
    install -m 0755 "$tmpdir/r4s/rate4site" "$target"
    rm -rf "$tmpdir"
    log "rate4site installed at ${target}"
}

# --- muscle5 ---
# Multiple sequence alignment engine used by conservation analysis for
# local alignment of orthologous sequences.  Pre-built static binary
# from upstream; Apache-2.0 licensed.
MUSCLE_VERSION="5.3"
MUSCLE_URL="https://github.com/rcedgar/muscle/releases/download/v${MUSCLE_VERSION}/muscle-linux-x86.v${MUSCLE_VERSION}"
MUSCLE_SHA256="318abeb951d786a3e2532714cc81ad3b3d8f79a2b517dc31316eeb5b694db2bc"

install_muscle() {
    local target="${BIN_DIR}/muscle"
    if [ -x "$target" ]; then
        log "muscle already installed at ${target}"
        return 0
    fi
    log "Downloading muscle ${MUSCLE_VERSION}"
    if ! curl -fsSL -o "$target" "$MUSCLE_URL" 2>/dev/null; then
        warn "Could not download muscle (network may be unavailable). Skipping."
        rm -f "$target"
        return 1
    fi

    local got
    got="$(sha256sum "$target" | cut -d' ' -f1)"
    if [ "$got" != "$MUSCLE_SHA256" ]; then
        warn "muscle checksum mismatch; refusing to install."
        warn "  expected ${MUSCLE_SHA256}"
        warn "  got      ${got}"
        rm -f "$target"
        return 1
    fi

    chmod +x "$target"
    log "muscle ${MUSCLE_VERSION} installed at ${target}"
}

# Map a tool name to the name of its SHA256 variable so the loop can
# detect PLACEHOLDER binaries generically.  Tools without a SHA256
# variable (vina downloads a fixed URL, rate4site pins a commit) return
# empty — they are never PLACEHOLDER.
_sha_var_for() {
    case "$1" in
        hypex) echo "HYPEX_SHA256" ;;
        elo)   echo "HYPEX_ELO_SHA256" ;;
        prox)  echo "HYPEX_PROX_SHA256" ;;
        *)     echo "" ;;
    esac
}

BINARY_STATUS=""
for tool in vina fpocket rate4site muscle hypex elo prox; do
    sha_var="$(_sha_var_for "$tool")"
    if [ -n "$sha_var" ] && [ "${!sha_var}" = "PLACEHOLDER" ]; then
        log "Binary ${tool} has no upstream release. Skipping."
        BINARY_STATUS="${BINARY_STATUS} ${tool}=PLACEHOLDER"
    elif "install_${tool}"; then
        BINARY_STATUS="${BINARY_STATUS} ${tool}=ok"
    else
        BINARY_STATUS="${BINARY_STATUS} ${tool}=MISSING"
    fi
done

# --- OpenBabel ---
# Chemical format conversion used by computational-chemist skills.
# Prefer system package if available; otherwise note it as missing.
if command -v obabel &>/dev/null; then
    log "OpenBabel found at $(command -v obabel)"
elif [ -x "${BIN_DIR}/obabel" ]; then
    log "OpenBabel found at ${BIN_DIR}/obabel"
else
    warn "OpenBabel (obabel) not found. Install via system package manager:"
    warn "  apt-get install -y openbabel   # Debian/Ubuntu"
    warn "  conda install -c conda-forge openbabel  # Conda"
fi

# ---------------------------------------------------------------------------
# Install the package as an editable console_scripts entry point
# ---------------------------------------------------------------------------
#
# `pip install --no-deps -e .` registers `dde = dde.cli:main` as a
# proper console script in the venv's bin/.  This replaces the old
# dde-cli shim that was symlinked by hand — the pip-managed wrapper
# works correctly in subshells and shell loops without PYTHONPATH.
#
# --no-deps because dependencies are already installed above from
# requirements.txt (and optionally requirements-science.txt) in separate
# transactions with separate failure semantics.  The deps declared in
# pyproject.toml mirror those files for metadata completeness but must
# not override the two-transaction install order.
#
# Guarded by BINARIES_ONLY: that flag means "touch no Python package",
# and on a previously provisioned venv the entry point is already set
# up from the initial install.

if [ "$BINARIES_ONLY" = false ]; then
    log "Installing dde package (editable, console_scripts entry point)"
    pip install --no-deps -e "${SCRIPT_DIR}" --quiet \
        || err "Editable install of dde package failed."
fi

# ---------------------------------------------------------------------------
# env.sh — the one thing an agent has to source
# ---------------------------------------------------------------------------
#
# Written by this script rather than maintained by hand on the volume.
# It was hand-maintained, and it had drifted: bin/ was never added to
# PATH, so every binary this script installs was invisible to anything
# that shells out by name — `dde doctor` reported fpocket present and
# unreachable in the same line. A file that provisioning depends on, and
# that provisioning does not write, is a file that describes an earlier
# installation.

if mkdir -p "$TOOLS_HOME_DIR" 2>/dev/null; then
    cat > "${TOOLS_HOME_DIR}/env.sh" <<ENVSH
# Shared dde tools environment. GENERATED by tools/install.sh —
# edit that script, not this file, or the next provisioning run
# discards your change.
#
#   source ${TOOLS_HOME_DIR}/env.sh
#   dde doctor
#
# Sourcing this is all you need. Do not pip install into this venv — it
# is shared, and a package added for one agent changes the env_version
# stamped into every other agent's provenance sidecars. Ask the tooling
# lead instead.

DDE_TOOLS_DIR="${TOOLS_HOME_DIR}"

# shellcheck disable=SC1091
source "\${DDE_TOOLS_DIR}/.venv/bin/activate"

# Read by dde/core/env.py to find the ENV_VERSION stamp. Without it
# every artifact records an "unpinned-dev" env_version and carries a
# warning saying its environment is not reproducible.
export DDE_TOOLS_HOME="\${DDE_TOOLS_DIR}"

# Provisioned binaries — fpocket, vina. They are hashed into
# ENV_VERSION, so a tool found here is a tool the provenance record can
# account for; one found elsewhere on PATH is not. Hence prepend.
export PATH="${BIN_DIR}:\${PATH}"

# The CLI source lives in the shared workspace, so several agents import
# the same files and write into the same __pycache__. An agent has
# already been served stale bytecode from before a commit it had pulled,
# and the failure looked like a bug in the tooling rather than a caching
# artifact. The cost of not caching is milliseconds; the cost of caching
# is an agent debugging code that is not the code that ran.
export PYTHONDONTWRITEBYTECODE=1

# Where artifacts land. Point this at your own program directory before
# running anything that writes, or let the CLI discover it via .dde/
# walk-up. The CLI produces a clear error with remedy text when it
# cannot resolve the project root (see core/context.py).

echo "dde \$(dde --version 2>/dev/null | awk '{print \$3}') from \${DDE_TOOLS_DIR}/.venv" >&2
ENVSH
    log "Wrote ${TOOLS_HOME_DIR}/env.sh"
else
    warn "Could not write ${TOOLS_HOME_DIR}/env.sh"
fi

# ---------------------------------------------------------------------------
# Environment stamp
# ---------------------------------------------------------------------------
#
# Every provenance sidecar records an `env_version`. When this file is
# present the CLI reports the hash it holds; when it is absent it falls
# back to hashing requirements.txt and warns in every sidecar that
# transitive dependencies are unpinned. Writing it here is what makes an
# artifact's environment reproducible after the fact.
#
# The stamp is computed by `dde env stamp`, not by this script. It
# hashes env-manifest.txt — interpreter, packages, and every file in
# bin/ with its own digest — and archives that manifest under its own
# hash so an old env_version in an old sidecar can still be explained.
#
# It used to be computed here, from `pip freeze` alone. Under that rule
# installing fpocket changed what the tools do and did not change the
# value that claims to identify them, and two binaries were one line of
# shell away from being invisible to provenance. One producer of the
# canonical document, and it is the one that can also read it back.

export DDE_TOOLS_HOME="${DDE_TOOLS_HOME:-/scion-volumes/tools}"
if mkdir -p "$DDE_TOOLS_HOME" 2>/dev/null; then
    if python -m dde.cli env stamp \
            --note "install.sh${BINARY_STATUS}"; then
        :
    else
        warn "Could not stamp ${DDE_TOOLS_HOME}; artifacts will record an"
        warn "unpinned developer env_version and carry a warning saying so."
    fi
else
    warn "Could not create ${DDE_TOOLS_HOME}; artifacts will record an"
    warn "unpinned developer env_version and carry a warning saying so."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "Setup complete."
echo ""
echo "  Core CLI dependencies: installed"
echo "  Science stack:         ${SCIENCE_STATUS}"
echo "  Binaries:             ${BINARY_STATUS}"
echo ""
# Point at env.sh, not at the venv's activate. This block used to say
# `source .venv/bin/activate`, and an agent that followed it got a
# working `dde` and a broken environment: no bin/ on PATH, so
# `dde pocket` reported fpocket missing, and no DDE_TOOLS_HOME, so
# the CLI read the compiled-in default, found no stamp there, and
# recorded `unpinned-dev` in every sidecar. The environment it had just
# built was invisible to it. Instructions that produce a silently
# degraded environment are worse than no instructions, because the agent
# has already been told it succeeded.
if [ -f "${TOOLS_HOME_DIR}/env.sh" ]; then
    echo "  Activate the environment (this, not the venv's activate — it also"
    echo "  puts bin/ on PATH and points the CLI at this tools home):"
    echo "    source ${TOOLS_HOME_DIR}/env.sh"
else
    # env.sh could not be written. Say so here rather than printing an
    # instruction to source a file that does not exist, and give the
    # three variables it would have set, because an agent can still
    # recover from those.
    warn "No env.sh at ${TOOLS_HOME_DIR}; set these by hand or nothing below works:"
    echo "    export DDE_TOOLS_HOME=${TOOLS_HOME_DIR}"
    echo "    export PATH=${BIN_DIR}:\$PATH"
    echo "    export PATH=${VENV_DIR}/bin:\$PATH"
fi
echo ""
echo "  Create a program to write artifacts into:"
echo "    dde init ~/my-program && export DDE_PROJECT=~/my-program"
echo ""
echo "  Check the installation — do this before trusting any result:"
echo "    dde doctor"
echo ""
echo "  Before changing this environment again, price the change first:"
echo "    dde env plan     # the ENV_VERSION it would produce, and why"
echo ""

# A partial install exits non-zero even though the CLI works, so that a
# provisioning script cannot record success for an environment that is
# missing half of what was asked for. Use --core-only to ask for the
# smaller environment deliberately and get a zero exit for it.
if [ "$SCIENCE_STATUS" = "failed" ]; then
    warn "Exiting non-zero: the science stack was requested and did not install."
    exit 3
fi

# Same rule for the binaries, and it is worth stating why the stamp was
# still written above. The environment is stamped for what it actually
# is, missing tool and all — a stamp that waits for a perfect install is
# a stamp that is absent exactly when artifacts are being produced by a
# partial one. But the script exits non-zero so nothing upstream records
# this as a completed provisioning, and `dde doctor` names the
# missing binary directly.
if [[ "$BINARY_STATUS" == *PLACEHOLDER* ]]; then
    log "Some declared binaries have no upstream release (PLACEHOLDER). This is expected."
    log "Update URLs and hashes in this script when releases are published."
fi

if [[ "$BINARY_STATUS" == *MISSING* ]]; then
    warn "Exiting non-zero: a declared binary did not install:${BINARY_STATUS}"
    exit 4
fi
