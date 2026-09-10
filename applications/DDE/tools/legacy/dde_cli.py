#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
DDE CLI — unified command-line toolkit for dde agent skills.

Provides deterministic data extraction wrappers around scientific tools,
APIs, and structured data formats used by dde agents. Each command
group corresponds to a domain or data source that one or more agent
skills depend on.

Usage:
    python3 dde_cli.py <group> <command> [options]

Command groups:
    coscientist   Extract data from Co-Scientist tournament JSON files
"""

import json
import os
import re
import sys
from collections import Counter

import click


# ============================================================================
# Top-level CLI group
# ============================================================================

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option("0.1.0", prog_name="dde")
def cli():
    """DDE CLI — toolkit for dde agent skills.

    Provides deterministic data extraction wrappers around scientific
    tools, APIs, and structured data formats used across the dde
    multi-agent pharmaceutical R&D system.
    """


# ============================================================================
# Helpers (shared across command groups)
# ============================================================================

def load_json(path):
    """Load and return parsed JSON, exiting on error."""
    if not os.path.isfile(path):
        click.echo(f"Error: File not found: {path}", err=True)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        click.echo(f"Error: Invalid JSON in {path}: {exc}", err=True)
        sys.exit(1)


def truncate(text, max_len=200):
    """Truncate text, appending ellipsis if needed."""
    if not text:
        return ""
    return text if len(text) <= max_len else text[:max_len].rstrip() + "..."


def safe_md(obj, *keys):
    """Safely traverse nested dicts to extract a markdown string."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
    return cur if isinstance(cur, str) else ""


# ============================================================================
# Command group: coscientist
# ============================================================================

DEFAULT_RAW_DIR = "/scion-volumes/scratchpad/raw-findings/"


@cli.group()
def coscientist():
    """Extract data from Co-Scientist tournament JSON files.

    Tournament files are typically 700 KB–1 MB and contain deeply nested
    structures covering research ideas, verification claims, knowledge
    bases, and executive reports.  These commands let agents pull exactly
    the slice they need without loading the entire file into context.
    """


# --- helpers specific to co-scientist data ---

def _gene_symbol(idea):
    for attr in idea.get("attributes", []):
        if attr.get("name") == "Gene Symbol":
            return attr.get("value")
    return None


def _find_idea(data, rank=None, gene=None):
    ideas = data.get("gs", [])
    if not ideas:
        click.echo("Error: No ideas found in this file.", err=True)
        sys.exit(1)
    if rank is not None:
        for idea in ideas:
            if idea.get("ranking") == rank:
                return idea
        valid = sorted(i.get("ranking") for i in ideas if i.get("ranking") is not None)
        click.echo(f"Error: No idea with rank {rank}. Valid ranks: {valid}", err=True)
        sys.exit(1)
    if gene is not None:
        gene_upper = gene.upper()
        for idea in ideas:
            sym = _gene_symbol(idea)
            if sym and sym.upper() == gene_upper:
                return idea
        available = [_gene_symbol(i) for i in ideas]
        available = [g for g in available if g]
        click.echo(f"Error: No idea with gene symbol '{gene}'. Available: {available}", err=True)
        sys.exit(1)
    click.echo("Error: Must specify --rank or --gene.", err=True)
    sys.exit(1)


def _fmt_elo(elo):
    return str(int(round(elo))) if isinstance(elo, float) else str(elo)


def _format_idea_section(idea, section, brief=False):
    """Format a specific section of an idea as markdown."""
    parts = []
    brief_limit = 500
    brief_suffix = "... [truncated, use without --brief for full output]"

    def maybe_truncate(text):
        if not brief or not text or len(text) <= brief_limit:
            return text
        return text[:brief_limit].rstrip() + brief_suffix

    gene = _gene_symbol(idea) or "N/A"
    rank = idea.get("ranking", "?")
    elo = _fmt_elo(idea.get("eloRating", "?"))

    def header():
        parts.append(f"# Idea Rank #{rank}: {idea.get('title', 'Untitled')}")
        parts.append(f"**Gene**: {gene} | **ELO**: {elo} | **Category**: {idea.get('category', 'N/A')}")
        parts.append("")

    if section in ("summary", "all"):
        header()
        parts.append("## Summary\n")
        parts.append(safe_md(idea, "summary", "markdown"))
        parts.append("")

    if section in ("description", "all"):
        if section == "description":
            header()
        parts.append("## Full Description\n")
        parts.append(maybe_truncate(safe_md(idea, "description", "markdown")))
        parts.append("")

    if section in ("attributes", "all"):
        if section == "attributes":
            header()
        parts.append("## Attributes\n")
        for attr in idea.get("attributes", []):
            name = attr.get("name", "?")
            value = attr.get("value", "")
            if len(str(value)) > 300:
                parts.append(f"### {name}\n")
                parts.append(str(value))
                parts.append("")
            else:
                parts.append(f"- **{name}**: {value}")
        parts.append("")

    if section in ("reviews", "all"):
        if section == "reviews":
            header()
        reviews = idea.get("reviews", [])
        parts.append(f"## Reviews ({len(reviews)} total)\n")
        for j, rev in enumerate(reviews, 1):
            score = rev.get("score", "?")
            rtype = rev.get("type", "?")
            parts.append(f"### Review {j} (Score: {score}, Type: {rtype})\n")
            parts.append(maybe_truncate(safe_md(rev, "text", "markdown")))
            refs = rev.get("references", [])
            if refs:
                parts.append(f"\n**References ({len(refs)}):**")
                for ref in refs:
                    parts.append(f"- [{ref.get('title', 'Untitled')}]({ref.get('source', '')})")
            parts.append("")

    if section in ("review-summary", "all"):
        if section == "review-summary":
            header()
        parts.append("## Reviews Summary\n")
        parts.append(maybe_truncate(safe_md(idea, "reviewsSummary", "markdown")))
        parts.append("")

    if section in ("verification-summary", "all"):
        if section == "verification-summary":
            header()
        parts.append("## Deep Verification Summary\n")
        dv = idea.get("deepVerification", {})
        parts.append(safe_md(dv, "verificationSummary", "markdown"))
        parts.append("")

    if section in ("claims", "all"):
        if section == "claims":
            header()
        dv = idea.get("deepVerification", {})
        claims = dv.get("claims", [])
        parts.append(f"## Deep Verification Claims ({len(claims)} total)\n")
        for j, claim in enumerate(claims, 1):
            verdict = claim.get("verdict", "?")
            parts.append(f"### Claim {j}: [{verdict}]")
            parts.append(f"**Claim**: {claim.get('claim', '')}")
            parts.append(f"**Source Sentence**: {claim.get('sourceSentence', '')}")
            parts.append(f"**Verdict**: {verdict}")
            parts.append(f"**Reasoning**: {claim.get('reasoning', '')}")
            refs = claim.get("references", [])
            if refs:
                parts.append(f"\n**References ({len(refs)}):**")
                for ref in refs:
                    parts.append(f"- [{ref.get('title', 'Untitled')}]({ref.get('source', '')})")
            parts.append("")

    if section in ("match-stats", "all"):
        if section == "match-stats":
            header()
        mr = idea.get("matchResult", {})
        parts.append("## Match Statistics\n")
        parts.append(f"- Total matches: {mr.get('numTotalMatches', '?')}")
        parts.append(f"- Wins: {mr.get('numMatchesWon', '?')}")
        parts.append(f"- Losses: {mr.get('numMatchesLost', '?')}")
        wr = mr.get("winRate")
        if wr is not None:
            parts.append(f"- Win rate: {wr * 100:.1f}%")
        parts.append("")

    return "\n".join(parts)


# --- coscientist subcommands ---

@coscientist.command("list-files")
@click.argument("directory", default=DEFAULT_RAW_DIR, required=False)
def cs_list_files(directory):
    """List available tournament JSON files in a directory."""
    if not os.path.isdir(directory):
        click.echo(f"Error: Directory not found: {directory}", err=True)
        sys.exit(1)

    json_files = sorted(f for f in os.listdir(directory) if f.endswith(".json"))
    if not json_files:
        click.echo(f"No JSON files found in {directory}")
        return

    click.echo(f"# Co-Scientist Tournament Files in {directory}\n")
    for i, fname in enumerate(json_files, 1):
        fpath = os.path.join(directory, fname)
        size_kb = os.path.getsize(fpath) / 1024
        try:
            data = load_json(fpath)
            title = data.get("title", "Untitled")
            goal = truncate(data.get("goal", ""), 120)
            num_ideas = data.get("stats", {}).get("numIdeas", "?")
            click.echo(f"## {i}. {title}")
            click.echo(f"- **File**: `{fname}`")
            click.echo(f"- **Goal**: {goal}")
            click.echo(f"- **Ideas generated**: {num_ideas}")
            click.echo(f"- **Size**: {size_kb:.0f} KB\n")
        except SystemExit:
            click.echo(f"## {i}. (Error reading file)")
            click.echo(f"- **File**: `{fname}`")
            click.echo(f"- **Size**: {size_kb:.0f} KB\n")


@coscientist.command()
@click.argument("json_file")
def overview(json_file):
    """Print a concise tournament summary."""
    data = load_json(json_file)

    title = data.get("title", "Untitled")
    goal = data.get("goal", "N/A")
    stats = data.get("stats", {})
    state = data.get("state", "UNKNOWN")
    stage = data.get("stage", "UNKNOWN")

    click.echo(f"# Tournament Overview: {title}\n")
    click.echo(f"**Goal**: {goal}\n")
    click.echo(f"**State**: {state} | **Stage**: {stage}\n")

    click.echo("## Statistics")
    click.echo(f"- Ideas generated: {stats.get('numIdeas', '?')}")
    click.echo(f"- Categories: {stats.get('numCategories', '?')}")
    click.echo(f"- Highest ELO: {_fmt_elo(stats.get('highestEloRating', '?'))}")
    click.echo(f"- Token usage: {stats.get('inputTokenCount', '?')} input / {stats.get('outputTokenCount', '?')} output\n")

    ideas = data.get("gs", [])
    if ideas:
        click.echo("## Top Ranked Ideas\n")
        click.echo("| Rank | Gene | ELO | Title |")
        click.echo("|------|------|-----|-------|")
        for idea in sorted(ideas, key=lambda x: x.get("ranking", 99)):
            gene = _gene_symbol(idea) or "N/A"
            elo = _fmt_elo(idea.get("eloRating", "?"))
            rank = idea.get("ranking", "?")
            idea_title = truncate(idea.get("title", "Untitled"), 80)
            mr = idea.get("matchResult", {})
            wr = mr.get("winRate")
            win_rate = f" ({wr * 100:.0f}% win rate)" if wr is not None else ""
            click.echo(f"| {rank} | {gene} | {elo} | {idea_title}{win_rate} |")
        click.echo()

    prefs = data.get("config", {}).get("preferences", [])
    if prefs:
        click.echo("## Tournament Preferences\n")
        for j, pref in enumerate(prefs, 1):
            click.echo(f"{j}. {pref}")
        click.echo()


@coscientist.command()
@click.argument("json_file")
@click.option("--rank", type=int, help="Select idea by ranking (1-5)")
@click.option("--gene", help="Select idea by gene symbol (e.g. FBXL19)")
@click.option("--section", default="summary",
              type=click.Choice(["summary", "description", "attributes", "reviews",
                                 "review-summary", "verification-summary", "claims",
                                 "match-stats", "all"]),
              help="Section to extract (default: summary)")
@click.option("--brief", is_flag=True, help="Truncate long sections to 500 chars")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]),
              help="Output format (default: markdown)")
def idea(json_file, rank, gene, section, brief, fmt):
    """Extract a single idea by rank or gene symbol."""
    if rank is None and gene is None:
        click.echo("Error: Must specify --rank or --gene.", err=True)
        sys.exit(1)

    data = load_json(json_file)
    found = _find_idea(data, rank=rank, gene=gene)

    if fmt == "json":
        if section == "all":
            click.echo(json.dumps(found, indent=2))
        else:
            section_map = {
                "summary": lambda i: i.get("summary", {}),
                "description": lambda i: i.get("description", {}),
                "attributes": lambda i: i.get("attributes", []),
                "reviews": lambda i: i.get("reviews", []),
                "review-summary": lambda i: i.get("reviewsSummary", {}),
                "verification-summary": lambda i: i.get("deepVerification", {}).get("verificationSummary", {}),
                "claims": lambda i: i.get("deepVerification", {}).get("claims", []),
                "match-stats": lambda i: i.get("matchResult", {}),
            }
            extractor = section_map.get(section)
            click.echo(json.dumps(extractor(found) if extractor else {}, indent=2))
    else:
        click.echo(_format_idea_section(found, section, brief=brief))


@coscientist.command()
@click.argument("json_file")
@click.option("--rank", type=int, help="Filter to a specific idea by rank")
@click.option("--gene", help="Filter to a specific idea by gene symbol")
@click.option("--verdict", help="Filter by verdict (e.g. INACCURATE, SUPPORTED)")
@click.option("--summary", "summary_mode", is_flag=True,
              help="Compact verdict count table instead of full claims")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]),
              help="Output format (default: markdown)")
def claims(json_file, rank, gene, verdict, summary_mode, fmt):
    """Extract deep verification claims."""
    data = load_json(json_file)
    ideas = data.get("gs", [])

    if rank is not None or gene is not None:
        found = _find_idea(data, rank=rank, gene=gene)
        ideas = [found]

    if summary_mode:
        for idea_item in sorted(ideas, key=lambda x: x.get("ranking", 99)):
            g = _gene_symbol(idea_item) or "N/A"
            r = idea_item.get("ranking", "?")
            dv = idea_item.get("deepVerification", {})
            cl = dv.get("claims", [])
            counts = Counter(c.get("verdict", "UNKNOWN") for c in cl)
            parts = ", ".join(f"{v} {k}" for k, v in counts.most_common())
            click.echo(f"Rank {r} ({g}): {len(cl)} claims — {parts}")
        return

    verdict_filter = verdict.upper() if verdict else None
    total_claims = 0
    shown_claims = 0
    all_claims_json = []

    for idea_item in ideas:
        g = _gene_symbol(idea_item) or "N/A"
        r = idea_item.get("ranking", "?")
        dv = idea_item.get("deepVerification", {})
        cl = dv.get("claims", [])

        idea_claims = []
        for c in cl:
            total_claims += 1
            v = c.get("verdict", "")
            if verdict_filter and v.upper() != verdict_filter:
                continue
            shown_claims += 1
            idea_claims.append(c)

        if not idea_claims:
            continue

        if fmt == "json":
            for c in idea_claims:
                all_claims_json.append({
                    "rank": r, "gene": g,
                    "claim": c.get("claim", ""),
                    "sourceSentence": c.get("sourceSentence", ""),
                    "verdict": c.get("verdict", ""),
                    "reasoning": c.get("reasoning", ""),
                    "references": c.get("references", []),
                })
        else:
            click.echo(f"# Claims for Rank #{r} ({g})\n")
            for j, c in enumerate(idea_claims, 1):
                v = c.get("verdict", "?")
                click.echo(f"## Claim {j}: [{v}]")
                click.echo(f"**Claim**: {c.get('claim', '')}")
                click.echo(f"**Source Sentence**: {c.get('sourceSentence', '')}")
                click.echo(f"**Verdict**: {v}")
                click.echo(f"**Reasoning**: {c.get('reasoning', '')}")
                refs = c.get("references", [])
                if refs:
                    click.echo(f"\n**References ({len(refs)}):**")
                    for ref in refs:
                        click.echo(f"- [{ref.get('title', 'Untitled')}]({ref.get('source', '')})")
                click.echo()

    if fmt == "json":
        click.echo(json.dumps(all_claims_json, indent=2))
    else:
        filter_note = f" (filtered by verdict='{verdict_filter}')" if verdict_filter else ""
        click.echo(f"---\n**Total claims**: {total_claims} | **Shown**: {shown_claims}{filter_note}")


@coscientist.command()
@click.argument("json_file")
@click.option("--search", help="Case-insensitive search in reference titles/URLs")
@click.option("--source", type=click.Choice(["knowledge-base", "overview", "idea"]),
              help="Which reference pool to search (default: all)")
@click.option("--rank", type=int, help="With --source idea, filter to a specific rank")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]),
              help="Output format (default: markdown)")
def references(json_file, search, source, rank, fmt):
    """Search references across knowledge base, overview, and ideas."""
    data = load_json(json_file)

    ref_pools = []

    if source is None or source == "knowledge-base":
        kb_refs = data.get("knowledgeBase", {}).get("references", [])
        ref_pools.append(("Knowledge Base", kb_refs))

    if source is None or source == "overview":
        eoa_refs = data.get("eOa", {}).get("Hxa", [])
        ref_pools.append(("Executive Overview (eOa.Hxa)", eoa_refs))

    if source is None or source == "idea":
        ideas = data.get("gs", [])
        if source == "idea" and rank is not None:
            found = _find_idea(data, rank=rank)
            ideas = [found]
        for idea_item in ideas:
            g = _gene_symbol(idea_item) or "N/A"
            r = idea_item.get("ranking", "?")
            for rev in idea_item.get("reviews", []):
                rev_refs = rev.get("references", [])
                if rev_refs:
                    ref_pools.append((f"Idea Rank #{r} ({g}) Review", rev_refs))
            dv = idea_item.get("deepVerification", {})
            for claim in dv.get("claims", []):
                claim_refs = claim.get("references", [])
                if claim_refs:
                    ref_pools.append((f"Idea Rank #{r} ({g}) Claim Verification", claim_refs))

    total_found = 0

    if fmt == "json":
        all_results = []
        for pool_name, refs in ref_pools:
            for ref in refs:
                title = ref.get("title", "")
                source_url = ref.get("source", "")
                if search and search.lower() not in title.lower() and search.lower() not in source_url.lower():
                    continue
                total_found += 1
                all_results.append({"pool": pool_name, "title": title, "source": source_url})
        click.echo(json.dumps(all_results, indent=2))
    else:
        for pool_name, refs in ref_pools:
            matches = []
            for ref in refs:
                title = ref.get("title", "")
                source_url = ref.get("source", "")
                if search and search.lower() not in title.lower() and search.lower() not in source_url.lower():
                    continue
                matches.append(ref)
            if not matches:
                continue
            total_found += len(matches)
            click.echo(f"## {pool_name} ({len(matches)} matches)\n")
            for ref in matches:
                click.echo(f"- [{ref.get('title', 'Untitled')}]({ref.get('source', '')})")
            click.echo()

    if total_found == 0:
        qualifier = f" matching '{search}'" if search else ""
        click.echo(f"No references found{qualifier}.")
    elif fmt != "json":
        click.echo(f"---\n**Total references found**: {total_found}")


@coscientist.command()
@click.argument("json_file")
@click.option("--section", default="summary",
              type=click.Choice(["summary", "connections", "full"]),
              help="Section to extract (default: summary)")
def knowledge(json_file, section):
    """Extract knowledge base content."""
    data = load_json(json_file)
    kb = data.get("knowledgeBase", {})

    if section in ("summary", "full"):
        click.echo("# Knowledge Base Summary\n")
        md = safe_md(kb, "knowledgeSummary", "markdown")
        click.echo(md if md else "(No knowledge summary available)")
        click.echo()

    if section in ("connections", "full"):
        click.echo("# Unexpected Connections Analysis\n")
        uxa = kb.get("Uxa", {})
        summary_md = safe_md(uxa, "summary", "markdown")
        click.echo(summary_md if summary_md else "(No connections analysis available)")
        click.echo()
        connections = uxa.get("connections", [])
        if connections:
            click.echo(f"## Individual Connections ({len(connections)} total)\n")
            for j, conn in enumerate(connections, 1):
                if isinstance(conn, dict):
                    conn_title = conn.get("title", conn.get("name", f"Connection {j}"))
                    conn_desc = conn.get("description", conn.get("markdown", ""))
                    click.echo(f"### {j}. {conn_title}")
                    if conn_desc:
                        click.echo(conn_desc)
                    click.echo()
                elif isinstance(conn, str):
                    click.echo(f"{j}. {conn}")
            click.echo()


@coscientist.command()
@click.argument("json_file")
@click.option("--section", default="all",
              type=click.Choice(["overview", "top-ideas", "reviews", "recommendations", "all"]),
              help="Section to extract (default: all)")
def report(json_file, section):
    """Extract executive report sections."""
    data = load_json(json_file)
    eoa = data.get("eOa", {})

    if not eoa:
        click.echo("Error: No executive overview (eOa) found in this file.", err=True)
        sys.exit(1)

    if section in ("overview", "all"):
        click.echo("# Executive Overview\n")
        click.echo(safe_md(eoa, "overview", "markdown"))
        click.echo()

    if section in ("top-ideas", "all"):
        click.echo("# Top Ranking Ideas Summary\n")
        click.echo(safe_md(eoa, "topRankingIdeasSummary", "markdown"))
        click.echo()

    if section in ("reviews", "all"):
        click.echo("# Reviews Overview\n")
        click.echo(safe_md(eoa, "reviewsOverview", "markdown"))
        click.echo()

    if section == "recommendations":
        top_md = safe_md(eoa, "topRankingIdeasSummary", "markdown")
        if not top_md:
            click.echo("No top-ranking ideas summary available.", err=True)
            sys.exit(1)
        match = re.search(
            r"(^##\s+Recommend.*?)(?=\n## (?!#)|\Z)",
            top_md, re.DOTALL | re.MULTILINE,
        )
        if match:
            click.echo("# Recommendations\n")
            click.echo(match.group(1).strip())
            click.echo()
        else:
            click.echo("No 'Recommendation' section found in the top-ranking ideas summary.", err=True)
            sys.exit(1)

    hxa = eoa.get("Hxa", [])
    if hxa:
        click.echo(f"---\n*{len(hxa)} references cited in executive overview*")


@coscientist.command()
@click.argument("json_file")
def categories(json_file):
    """Explore the category landscape of tournament ideas."""
    data = load_json(json_file)
    ideas = data.get("gs", [])

    if not ideas:
        click.echo("Error: No ideas found in this file.", err=True)
        sys.exit(1)

    category_info = {}
    for idea_item in sorted(ideas, key=lambda x: x.get("ranking", 99)):
        cat = idea_item.get("category", "Uncategorized")
        elo = idea_item.get("eloRating", 0)
        r = idea_item.get("ranking", "?")
        gene = _gene_symbol(idea_item) or "N/A"
        if cat not in category_info:
            category_info[cat] = {"count": 0, "best_elo": 0, "ideas": []}
        category_info[cat]["count"] += 1
        if elo > category_info[cat]["best_elo"]:
            category_info[cat]["best_elo"] = elo
        category_info[cat]["ideas"].append(f"Rank {r} ({gene})")

    sorted_cats = sorted(category_info.items(), key=lambda x: x[1]["best_elo"], reverse=True)
    title = data.get("title", "Untitled")
    click.echo(f"# Categories: {title}\n")
    click.echo(f"**{len(sorted_cats)} categories** across {len(ideas)} top ideas\n")
    click.echo("| # | Category | Ideas | Best ELO | Members |")
    click.echo("|---|----------|-------|----------|---------|")
    for i, (cat, info) in enumerate(sorted_cats, 1):
        cat_display = truncate(cat, 70)
        members = ", ".join(info["ideas"])
        click.echo(f"| {i} | {cat_display} | {info['count']} | {_fmt_elo(info['best_elo'])} | {members} |")
    click.echo()


@coscientist.command("ideas-table")
@click.argument("json_file")
def cs_ideas_table(json_file):
    """Side-by-side comparison table of all top ideas."""
    data = load_json(json_file)
    ideas = data.get("gs", [])

    if not ideas:
        click.echo("Error: No ideas found in this file.", err=True)
        sys.exit(1)

    title = data.get("title", "Untitled")
    click.echo(f"# Ideas Comparison: {title}\n")
    click.echo("| Rank | Gene/Target | ELO | Modality | Key Mechanism (MoA) | Primary Flaw | Win Rate |")
    click.echo("|------|-------------|-----|----------|---------------------|--------------|----------|")

    for idea_item in sorted(ideas, key=lambda x: x.get("ranking", 99)):
        r = idea_item.get("ranking", "?")
        elo = _fmt_elo(idea_item.get("eloRating", "?"))
        attrs = {a.get("name"): a.get("value") for a in idea_item.get("attributes", [])}
        gene = attrs.get("Gene Symbol", "N/A")
        modality = truncate(str(attrs.get("Modality", "N/A")), 30)
        moa = truncate(str(attrs.get("MoA", "N/A")), 40)

        mr = idea_item.get("matchResult", {})
        wr = mr.get("winRate")
        win_rate_str = f"{wr * 100:.0f}%" if wr is not None else "N/A"

        primary_flaw = "N/A"
        dv = idea_item.get("deepVerification", {})
        dv_claims = dv.get("claims", [])
        inaccurate = [c for c in dv_claims
                      if c.get("verdict", "").upper() in ("INACCURATE", "LEANING_INACCURATE")]
        if inaccurate:
            primary_flaw = truncate(inaccurate[0].get("claim", "N/A"), 60)
        elif dv_claims:
            primary_flaw = "No major flaws identified"

        click.echo(f"| {r} | {gene} | {elo} | {modality} | {moa} | {primary_flaw} | {win_rate_str} |")

    click.echo()
    elos = [i.get("eloRating", 0) for i in ideas]
    click.echo(f"**ELO range**: {_fmt_elo(min(elos))} – {_fmt_elo(max(elos))}")
    wrs = [i.get("matchResult", {}).get("winRate") for i in ideas]
    wrs = [w for w in wrs if w is not None]
    if wrs:
        click.echo(f"**Win rate range**: {min(wrs) * 100:.0f}% – {max(wrs) * 100:.0f}%")


@coscientist.command()
@click.argument("json_file1")
@click.argument("json_file2")
@click.option("--aspect", default="genes",
              type=click.Choice(["genes", "rankings", "elo"]),
              help="What to compare (default: genes)")
def compare(json_file1, json_file2, aspect):
    """Compare top ideas across two tournament files."""
    data1 = load_json(json_file1)
    data2 = load_json(json_file2)

    title1 = data1.get("title", "File 1")
    title2 = data2.get("title", "File 2")
    ideas1 = data1.get("gs", [])
    ideas2 = data2.get("gs", [])

    click.echo("# Tournament Comparison\n")
    click.echo(f"**Tournament A**: {title1}")
    click.echo(f"**Tournament B**: {title2}\n")

    if aspect == "genes":
        genes1, genes2 = set(), set()
        click.echo("## Tournament A - Top Ideas")
        click.echo("| Rank | Gene | ELO | Title |")
        click.echo("|------|------|-----|-------|")
        for idea_item in sorted(ideas1, key=lambda x: x.get("ranking", 99)):
            gene = _gene_symbol(idea_item) or "N/A"
            genes1.add(gene)
            click.echo(f"| {idea_item.get('ranking', '?')} | {gene} | {_fmt_elo(idea_item.get('eloRating', '?'))} | {truncate(idea_item.get('title', ''), 60)} |")
        click.echo()

        click.echo("## Tournament B - Top Ideas")
        click.echo("| Rank | Gene | ELO | Title |")
        click.echo("|------|------|-----|-------|")
        for idea_item in sorted(ideas2, key=lambda x: x.get("ranking", 99)):
            gene = _gene_symbol(idea_item) or "N/A"
            genes2.add(gene)
            click.echo(f"| {idea_item.get('ranking', '?')} | {gene} | {_fmt_elo(idea_item.get('eloRating', '?'))} | {truncate(idea_item.get('title', ''), 60)} |")
        click.echo()

        shared = genes1 & genes2
        only_a = genes1 - genes2
        only_b = genes2 - genes1
        click.echo("## Overlap Analysis")
        click.echo(f"- **Shared genes**: {', '.join(sorted(shared)) if shared else 'None'}")
        click.echo(f"- **Only in A**: {', '.join(sorted(only_a)) if only_a else 'None'}")
        click.echo(f"- **Only in B**: {', '.join(sorted(only_b)) if only_b else 'None'}\n")

    elif aspect == "rankings":
        click.echo("## Rankings Comparison\n")
        click.echo("| Rank | Tournament A | Tournament B |")
        click.echo("|------|-------------|-------------|")
        max_rank = max(
            max((i.get("ranking", 0) for i in ideas1), default=0),
            max((i.get("ranking", 0) for i in ideas2), default=0),
        )
        for r in range(1, max_rank + 1):
            a_name = b_name = ""
            for i in ideas1:
                if i.get("ranking") == r:
                    gene = _gene_symbol(i) or ""
                    a_name = f"{gene}: {truncate(i.get('title', ''), 50)}"
            for i in ideas2:
                if i.get("ranking") == r:
                    gene = _gene_symbol(i) or ""
                    b_name = f"{gene}: {truncate(i.get('title', ''), 50)}"
            click.echo(f"| {r} | {a_name} | {b_name} |")
        click.echo()

    elif aspect == "elo":
        click.echo("## ELO Ratings Comparison\n")
        all_entries = []
        for idea_item in ideas1:
            gene = _gene_symbol(idea_item) or "N/A"
            all_entries.append({"source": "A", "gene": gene, "elo": idea_item.get("eloRating", 0),
                                "rank": idea_item.get("ranking", "?"), "title": truncate(idea_item.get("title", ""), 50)})
        for idea_item in ideas2:
            gene = _gene_symbol(idea_item) or "N/A"
            all_entries.append({"source": "B", "gene": gene, "elo": idea_item.get("eloRating", 0),
                                "rank": idea_item.get("ranking", "?"), "title": truncate(idea_item.get("title", ""), 50)})
        all_entries.sort(key=lambda x: x["elo"], reverse=True)
        click.echo("| Source | Rank | Gene | ELO | Title |")
        click.echo("|--------|------|------|-----|-------|")
        for e in all_entries:
            click.echo(f"| {e['source']} | {e['rank']} | {e['gene']} | {_fmt_elo(e['elo'])} | {e['title']} |")
        click.echo()

    # Stats comparison
    stats1 = data1.get("stats", {})
    stats2 = data2.get("stats", {})
    click.echo("## Statistics Comparison\n")
    click.echo("| Metric | Tournament A | Tournament B |")
    click.echo("|--------|-------------|-------------|")
    click.echo(f"| Ideas generated | {stats1.get('numIdeas', '?')} | {stats2.get('numIdeas', '?')} |")
    click.echo(f"| Categories | {stats1.get('numCategories', '?')} | {stats2.get('numCategories', '?')} |")
    click.echo(f"| Highest ELO | {_fmt_elo(stats1.get('highestEloRating', '?'))} | {_fmt_elo(stats2.get('highestEloRating', '?'))} |")


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    cli()
