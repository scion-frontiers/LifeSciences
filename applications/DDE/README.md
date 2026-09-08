# DDE

Agentic pre-clinical pharmaceutical R&D system built on [scion](https://github.com/scion-frontiers/scion).

DDE provides the agent templates, skills, tools, and artifact conventions needed to stand up a coordinated multi-agent research team for any drug discovery program. Give the Science Program Lead a scientific objective — a disease indication, a target hypothesis, a modality — and it directs the program through versioned scientific work orders. A persistent Research Operations Controller supervises ephemeral specialists, validates their artifacts, and publishes accepted results. The science lead keeps sole authority over evidence acceptance and program decisions.

This README is a summary. [**docs/dde-plan.md**](docs/dde-plan.md) is the source of truth for the design.

## Project Status

The design is settled and recorded in the plan. The build is in progress.

**This section is the least trustworthy part of the README.** It is a cache of the
repository's contents, written on a date and revalidated by nobody, and it decays every
time a tool or skill lands. Do not plan from it. The repository answers the same
questions in ten seconds and cannot be stale:

```bash
ls skills/                                           # skills that exist
ls templates/                                        # agent templates that exist
grep -c uri: templates/*/scion-agent.yaml            # how many each template grants
grep -h uri: templates/*/scion-agent.yaml | sort -u  # which skills those are
dde doctor                                        # which tools are installed and callable
```

**If those disagree with anything below, they are right and this is stale.**

The `-c` line is there for a reason and should be run first. If the field were
renamed or the templates moved, the `sort -u` line would print nothing — and
nothing reads as *no template grants any skill*, a false statement about the
repository, rather than as *this command stopped working*. The `-c` form cannot
do that: it names every file it read and prints a count for each, so a rename
shows up as twelve zeros or as `No such file`, both of which are obviously the
instrument and not the repository. Prefer the form whose failure is loud.

The build order is the durable part, and it is a design statement rather than a status
report: **tooling, then skills, then templates.** A skill cannot name an invocation that
does not exist, and a template cannot grant a skill nobody has written. Work moves down
that order, never up.

**Snapshot — 2026-08-18, decays from that moment.** The pilot tools (co-scientist,
AlphaFold, AlphaGenome) are built, and further tool groups have landed since; `dde
doctor` lists what is actually callable. Nine skills exist. All twelve templates,
including the controller and the reviewer, grant dde capability skills — the earlier
state, in which templates granted upstream `science-skills` URIs directly, is gone.
Coverage is uneven by role rather than uniformly early: some roles hold several
capability skills, some hold one, and some hold none and will report blocked by design.

No number is given here for the last group, because the obvious way to count it is
wrong. `grep -c` counts *granted* skills, and every template is granted
`artifact-conventions`, which is a convention rather than a capability —
`program-state-management` likewise. A role showing `1` may hold no capability at all.
Read the URIs, not the counts, and decide per skill which kind it is. The per-role
picture is in each template's `scion-agent.yaml` and, for the planning view, in
`templates/science-program-lead/agents.md` §9.

The three guidance documents are v0.1. The pilot is expected to change them. See [dde-plan.md §10](docs/dde-plan.md).

## How It Works

```bash
# Create a Science Program Lead for a new research program
scion start program-lead --type science-program-lead

# Give it a scientific objective
scion message agent:program-lead "Investigate PCSK9 as a target for familial
hypercholesterolemia. Small molecule modality. Evaluate druggability and
advance through starting matter identification."
```

The Science Program Lead creates a Research Operations Controller, defines the initial decision questions, and commits work orders with immutable context snapshots. The controller initializes the project, supervises specialist runs, performs mechanical artifact validation, and keeps the presentation layer synchronized. The science lead accepts or rejects interpretations, maintains Layer 2 program state, and routes the work that follows.

## The Pipeline

### Stage 0: Hypothesis Entry

Before the four invariant stages begin, a program acquires its initial hypothesis set through one of four strategies: sponsor-supplied adoption, charter-authored hypotheses, a Co-Scientist tournament export, or a hypex tournament run within the program. Stage 0 is how a program acquires something to take into Stage 1.

### The Four Invariant Stages

DDE follows the pre-clinical drug discovery value chain. The stages are invariant and modality-independent. What happens within each stage is dynamic — which roles participate, which workflows execute, and how work is sequenced all depend on the evolving scientific context.

| Stage | Objective | Gate question |
|---|---|---|
| **1. Identify & Validate the Intervention Point** | Link a molecular intervention point to the disease and confirm it is tractable for the modality | Is the causal evidence sufficient, and is the intervention point tractable? |
| **2. Find Starting Matter** | Identify active entities that engage the intervention point, confirm activity, and remove artifacts | Do we have confirmed, tractable starting matter with a path to optimization? |
| **3. Multiparameter Optimization** | Evolve starting matter into entities that meet efficacy, safety, and developability needs together | Does an optimized entity meet all critical quality attributes? |
| **4. Demonstrate Human Readiness** | Produce the safety, efficacy, pharmacology, and regulatory package for first-in-human studies | Is the risk-benefit acceptable for human dosing? |

Gate criteria are named constants with program overrides in `.dde/thresholds.yaml`. Every analysis output records the threshold set it applied. Thresholds never live in prose, because prose cannot be enforced, versioned, or varied per program. See [dde-plan.md §3](docs/dde-plan.md) for the criteria and how they vary by modality.

## Agent Roles

Specialist roles are stage-agnostic. Each definition describes *who the specialist is*, not what stage-specific work it does. Stage-specific questions and context arrive through versioned work orders.

| Role | Description |
|---|---|
| **Science Program Lead** | Owns scientific work planning, evidence acceptance, Layer 2 state, and stage decisions. The only agent the user creates directly. Template: `science-program-lead`. |
| **Research Operations Controller** | Persistent controller for work-order execution, agent lifecycle, resource scheduling, artifact validation, and publication. |
| **Scientific Reviewer** | Independent evidence audit for gate-critical, high-impact, or disputed findings. |
| **Structural Biologist** | Protein structure prediction, druggability assessment, co-crystal SAR interpretation, selectivity engineering. |
| **Computational Biologist** | Genomic target nomination, GWAS fine-mapping, multi-omic analysis, variant interpretation. |
| **Computational Chemist** | Virtual screening, docking campaigns, FEP/RBFE calculations, ML ADMET prediction. |
| **Medicinal Chemist** | Hit tractability, scaffold selection, SAR-driven analog design, bioisostere strategy. |
| **Experimental Biologist** | CRISPR validation, HTS execution, dose-response, cellular potency assays, in vivo efficacy models. |
| **ADMET/DMPK Scientist** | Metabolic stability, CYP inhibition, permeability, in vivo PK, DDI risk assessment. |
| **Preclinical Toxicologist** | GLP toxicology study design, NOAEL determination, safety pharmacology, risk assessment. |
| **Regulatory Scientist** | IND dossier assembly, GLP compliance, CMC documentation, regulatory strategy. |
| **Project Curator** | Optional editorial role for executive narrative or new stakeholder views. Deterministic tools maintain site synchronization. |

## Design Principles

Six principles shape the system. [dde-plan.md §2](docs/dde-plan.md) gives the reasoning behind each.

- **Do not re-teach the LLM what it knows.** Role templates give a persona trigger, a capability grant, and an output contract — not re-taught domain knowledge.
- **Invariant structure, dynamic content.** The four stages are fixed. Everything within them is selected by the Science Program Lead from the evolving context.
- **Separate reporting from reasoning.** Gate documents are retrospective deliverables. The science lead reasons against the living program state.
- **Ephemeral specialists, rich task context.** Each specialist receives an immutable work order with a bounded, checksummed context snapshot.
- **Computation in tools, judgment in skills.** Anything computable belongs in the CLI. Anything that is a threshold belongs in its configuration.
- **One scientific authority, separate operational control.** The controller executes; it cannot change a question, accept an interpretation, or advance a gate.

## Artifact Architecture

Project artifacts are organized into five layers of increasing abstraction:

| Layer | Name | Contents |
|---|---|---|
| **0** | Raw Tool I/O | Everything a tool writes: docking JSONs, RDKit descriptors, AlphaFold structures, assay CSVs, provenance sidecars, and analysis verdicts |
| **1** | Specialist Findings | The prose conclusion of a decision step — what a role concluded after weighing Layer 0 output against program context |
| **2** | Program State | The Science Program Lead's decision surface: active series, liabilities, decision log, open questions |
| **3** | Stage Gate Documents | Intervention Validation Package, Starting Matter Declaration, Candidate Nomination Dossier, Human Readiness Package |
| **4** | Executive Summary | One-page program status for portfolio-level stakeholders |

The boundary between Layer 0 and Layer 1 is the boundary between *computed* and *judged*. It makes review mechanical: a reviewer re-runs `analyze` against the stored artifact, diffs the result, then asks whether the finding's prose is supported by it.

Every factual claim in a report links to its supporting artifact — vertically to raw data, laterally to peer findings, or upward to program state. These links give both audit trails and the navigation structure for deterministic website and dashboard builds.

Orchestration records — work orders, run history, publication state — live in a separate control plane under `.dde/control/`. They are auditable, but they are not scientific citation sources.

## Repository Structure

```
dde/
├── templates/          # Scion agent templates, one directory per role
├── skills/             # DDE skills, one directory per capability
├── tools/              # The dde CLI and its environment
│   ├── BOOTSTRAP.md    # Blank directory to working CLI, incl. container prereqs
│   ├── bootstrap-preflight.sh   # Can this container build it? Run before install.sh
│   ├── install.sh      # Environment setup: venv, compiled binaries, ENV_VERSION
│   ├── requirements.txt
│   ├── pyproject.toml  # PEP 621 packaging; console_scripts entry point
│   ├── legacy/         # The pre-contract CLI, kept for reference
│   └── dde/
│       ├── cli.py      # Click entry point; one command group per tool
│       ├── core/       # Project root, HTTP, provenance, thresholds, output
│       └── commands/   # One module per tool; phases as subcommands
├── docs/               # Design documents
├── LICENSE
└── README.md
```

[dde-plan.md §7](docs/dde-plan.md) shows the target structure, including the directories that steps 2 and 3 add.

Each agent template contains three files:

- **`scion-agent.yaml`** — schema, description, and the `skills:` capability grant
- **`system-prompt.md`** — persona trigger, 1-2 sentences
- **`agents.md`** — role instructions, questions owned, output contract

The `skills:` list is the only declaration of what an agent can do. A template must not restate it in prose, because a second copy drifts.

## Tools

The `tools/` directory holds the `dde` CLI. The CLI is the execution surface for every computation an agent performs. A specialist never computes a reported value itself. If a required tool is unavailable, the specialist reports the task as blocked rather than estimating.

Two rules shape every command:

- **Two phases.** `fetch` or `run` performs the expensive act and writes the artifact with a provenance sidecar. `analyze` reads that artifact from disk and applies named thresholds. The second phase re-runs without repeating the first — after a threshold change, or as a reviewer's fabrication check.
- **Everything a tool writes is Layer 0.** Tool output lands under `raw/`, resolved from `$DDE_PROJECT` or a `.dde/` marker, never from the working directory. Only a specialist writes to `findings/`.

### Bootstrapping a tools environment

**Starting from a blank directory in a fresh container, see
[`tools/BOOTSTRAP.md`](tools/BOOTSTRAP.md).** It covers what has to be
installed in the bootstrapper's *own* container before `install.sh` can
run — a C toolchain and the static archives, because `fpocket` is
compiled here rather than downloaded — and what to do when you lack the
privilege to install it. Run `tools/bootstrap-preflight.sh` first: it
writes nothing, needs no privilege, and prints the exact `apt-get` line
for whatever is missing, before anyone has waited fifteen minutes to
find out.

The summary below assumes an already-equipped container.

`install.sh` provisions a complete environment into a blank directory: a
venv, the pip stack, the compiled binaries (`vina`, a statically linked
`fpocket`), and the `ENV_VERSION` stamp. Three variables place it, and a
shared volume is the normal case — one environment several agents run
out of, rather than a copy each.

```bash
cd tools
DDE_VENV=/scion-volumes/tools/.venv \
DDE_BIN=/scion-volumes/tools/bin \
DDE_TOOLS_HOME=/scion-volumes/tools \
  ./install.sh
```

Omit all three and everything lands under `tools/` for a local checkout.
`install.sh` writes `env.sh` into the tools home, and **that is the file
to source — not the venv's `activate`**:

```bash
source /scion-volumes/tools/env.sh

dde init ~/my-program && export DDE_PROJECT=~/my-program
dde doctor
```

`env.sh` sets three things `activate` does not: `bin/` on `PATH`, so
`fpocket` and `vina` resolve; `DDE_TOOLS_HOME`, so the CLI reads the
stamp for *this* environment rather than a compiled-in default; and
`PYTHONDONTWRITEBYTECODE`, because agents share one `__pycache__` on a
shared volume. Activating the venv alone yields a working `dde` and a
silently degraded environment — missing binaries, and `unpinned-dev`
written into every artifact's provenance.

**Run `dde doctor` before trusting any result, and read its exit
code, not just its output.** It converts a missing tool from an invented
number into a blocked task, and it fails when the environment has
drifted from its own stamp or was provisioned from a commit nobody else
can fetch.

`install.sh` exits non-zero on a partial install (3 = science stack, 4 =
a declared binary missing) even though the CLI works, so provisioning
cannot be recorded as complete for an environment that is missing half
of what was asked for. It is safe to re-run; re-running is free and does
not churn `ENV_VERSION`.

### Changing the environment

An environment change partitions the program's artifacts: results either
side of it carry different `env_version` values and are not directly
comparable. Price the change before making it.

```bash
dde env plan      # what ENV_VERSION would become, and what differs
dde env show      # what it is now, and whether it still matches
dde env diff <before> [after]   # explain a partition after the fact
```

Command groups today: `alphafold`, `alphagenome`, `coscientist`,
`doctor`, `env`, `expression`, `genetics`, `init`, `litref`, `pocket`,
`relays`. The single-file CLI that predated the two-phase contract is
kept in `tools/legacy/`, with a write-up of the silent failure it hid.
Four of its subcommands are not yet ported.

## Working in a shared checkout

Several agents may share one working tree and one git index at the same time
(`SCION_WORKSPACE_MODE=shared-plain`). Two consequences, both of which have already
cost work here:

- **Commit by explicit path: `git commit --only <paths> -m ...`.** `git add <path>`
  protects others from your *add*; it does nothing about your *commit*, which takes the
  whole index including anything someone else staged seconds earlier. One commit here
  swept two files belonging to another agent under an unrelated subject line, and the
  next would have taken nine. `--only` commits the named paths and leaves the rest of
  the index staged and intact.
- **`--only` takes those paths from the working tree, not from the index.** Verified,
  because it is the one place the flag is not simply a narrower `add`: if you staged
  part of a file and left the rest unfinished, `--only <that file>` commits the
  unfinished version too. Staging a subset of hunks protects you from `git commit`; it
  does not protect you from `git commit --only`. Check with `git diff -- <paths>` before
  committing, not `git diff --cached`.
- **Put the reasoning where it will be read, not in the commit message.** In a tree
  where authorship is not durable, a rationale in a commit message can quietly change
  owner or vanish with a sweep, and it is read during archaeology rather than while
  deciding. A rationale belongs at the distance the reader will be standing when the
  question occurs to them: in the docstring if it is read while editing, in the output
  string if it is read while deciding. `dde doctor`'s advisories carry the test that
  would retire them in the remedy field for exactly this reason — the same words in a
  commit message would be correct and unreachable.

Before assuming a file is unchanged, re-read it: `git status --porcelain` reflects
everyone. A clean run of your own commits is not evidence the practice is safe — it may
only mean nobody else was mid-stage at the time.

**The expensive version of this is not misattribution.** A swept file arriving under
someone else's subject line costs a commit message. What costs more is *your
half-finished edit going out under a commit that claims it was reviewed*, and then
pushed — the tree is shared, so an intermediate state you were still thinking about can
become the published state of the repository without you touching a key. So: prefer to
leave a file untouched until you can finish it in one pass, and if you must stop
mid-edit, either commit the coherent part by explicit path or expect the rest to be
adopted by whoever commits next.

## Design Documentation

- **[docs/dde-plan.md](docs/dde-plan.md)** — the source of truth: stages, roles, artifact architecture, repository structure, and the build sequence.
- **[docs/orchestration-design-guidance.md](docs/orchestration-design-guidance.md)** — normative for the authority model and the end-to-end orchestration contract: work orders, control state, agent lifecycle, validation, scientific acceptance, escalation, and publication.
- **[docs/skill-design-guidance.md](docs/skill-design-guidance.md)** — normative for writing dde skills. Skills own *when* to run something, *what* to run, *where* results land, and *what they mean*. Tools group into capabilities rather than one skill per tool, so skills stay specialist-neutral.
- **[docs/tool-design-guidance.md](docs/tool-design-guidance.md)** — normative for the tools environment, the CLI, and the artifact contract: two-phase invocation, provenance sidecars, threshold configuration, and output discipline.
- **[docs/pilot-handoff.md](docs/pilot-handoff.md)** — not normative, and the one to read first if you are inheriting this repository: what the first pilot can actually do, which refusals are designed behaviour rather than defects, and how to triage a report from a pilot team.

## Contributing

See [docs/contributing.md](docs/contributing.md).

## License

Apache 2.0 — see [LICENSE](LICENSE).
