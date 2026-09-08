"""Provenance sidecars and analysis records.

Every phase-1 artifact gets `<name>.meta.json` beside it; every phase-2
run emits `<name>.analysis.json` citing its source and the threshold set
applied. See docs/tool-design-guidance.md §5.

`warnings` on the sidecar is load-bearing: warnings that must reach the
report are captured here so a reviewer can confirm the specialist
relayed them.

Some warnings are stronger than that. A warning carrying a `relay` code
is a **mandatory relay** in the sense of skill-design-guidance §4.5: it
must reach the Layer 1 finding unchanged, because a report that omits it
is wrong rather than merely incomplete. Isoform substitution is the
model case — the numbers describe a different molecule than the one the
finding names.

These carry a stable code (`afdb.partial_coverage`) as well as prose.
The code is what makes the reviewer check mechanical: prose is rewritten
between builds and cannot be matched on, but a skill can name a code and
a reviewer can enumerate the codes in an artifact and confirm each was
addressed. Codes are registered in RELAY_CODES so the set is
discoverable rather than scattered across call sites.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import env, output
from .errors import ArtifactError, Refusal


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Fields that differ between two runs that agree. Excluded from the
#: overwrite comparison so an idempotent re-run stays frictionless: what
#: matters is whether the *verdict* would change, not whether the clock
#: moved.
#:
#: `written_by` is here too, and that is only safe because of what
#: `_may_write` now does with the answer: an agreeing record is kept,
#: not rewritten, so a field excluded from the comparison can no longer
#: be lost by a write that the comparison called harmless. The exclusion
#: and the keep are one mechanism; either alone is a defect.
_VOLATILE_ANALYSIS_FIELDS = ("timestamp", "written_by")

#: Set by the phase-2 wrapper when `--overwrite` was passed. A latch
#: rather than an argument for the same reason as the network ban: an
#: argument has to be threaded through every call site, and the call
#: sites are what the guard exists to constrain.
_overwrite_allowed = False


def allow_overwrite(allowed: bool) -> None:
    """Permit this invocation to replace a differing analysis record."""
    global _overwrite_allowed
    _overwrite_allowed = allowed


def _writer() -> str | None:
    """Which agent ran this. Identity, never a credential."""
    return os.environ.get("SCION_AGENT_SLUG") or None


def _work_order() -> str | None:
    """Which work order this artifact was produced under."""
    return os.environ.get("DDE_WORK_ORDER_ID") or None


# Registry of mandatory-relay codes. A skill's interpretation contract
# names these; `dde relays` prints them so skill authors and reviewers
# work from the same list instead of from prose that has since changed.
#
# Each entry is written as an **obligation on the finding**, not as a
# description of the condition. A relay is an instruction, not a caveat:
# `afdb.partial_coverage` does not mean "mention that coverage is
# partial", it means "scope the claim to the modelled region or withhold
# it". Phrased as a description, a skill author satisfies the relay by
# quoting it, and a 20%-covered model gets a finding that discloses the
# truncation and still calls the protein well ordered.
# Shared vocabulary that currently lives above the CLI layer: reading
# this dict by import drags in output, and output imports click. Anything
# outside the venv that wants the registered codes — a checker, a doc
# generator — therefore parses the literal out of this file instead
# (tools/check_threshold_names.py, which exits 2 rather than passing if
# the literal is not found here).
#
# The clean fix is to move this to a leaf module and re-export it. Not
# done, deliberately: there is one such consumer, it is unblocked, and
# the move would break its file assumption for no present gain. The
# trigger to do it is a *second* out-of-tree reader — at that point the
# parse-by-hand workaround stops being one file's quirk and becomes the
# interface.
RELAY_CODES: dict[str, str] = {
    "allen.brain_region_expression_only": (
        "Allen Brain Atlas expression data covers brain regions only. "
        "Expression in peripheral tissues (skin, DRG, etc.) is not "
        "represented. Do not infer absence of expression in non-brain "
        "tissues from this dataset."
    ),
    "afdb.partial_coverage": (
        "Scope every confidence claim to the modelled residue range, or "
        "withhold it. Do not describe the protein — describe the fragment."
    ),
    "afdb.isoform_substituted": (
        "Name the isoform actually analysed wherever the finding names the "
        "protein. Do not attribute these numbers to the canonical entry."
    ),
    "afdb.above_modelling_limit": (
        "State that no full-length prediction exists at any accession. Do "
        "not present the available fragments as the protein's structure."
    ),
    "afdb.coverage_unverified": (
        "Report coverage as unknown. Do not infer completeness from the "
        "absence of a truncation warning."
    ),
    "afdb.confidence_is_not_accuracy": (
        "Do not treat high pLDDT as agreement with an experimental structure "
        "or as confirmation that this is the biologically relevant "
        "conformation. pLDDT is the model's confidence in its own prediction; "
        "a high score means the prediction is self-consistent, not that it "
        "matches reality. Do not write 'the structure is accurate' — write "
        "'the predicted fold is high-confidence.'"
    ),
    "afdb.model_is_not_docking_ready": (
        "Do not use this model for docking or binding-site work without "
        "stating that it is a prediction, not an experimental structure. "
        "Side-chain and loop conformations are the parts pLDDT is least "
        "informative about, and they are what docking depends on. A "
        "high-pLDDT model is a fold hypothesis, not a docking-ready "
        "receptor."
    ),
    "fpocket.conformation_dependent": (
        "Say which conformation was scored, and do not convert a low score "
        "into a claim about the target. fpocket's druggability model was "
        "trained on crystal structures and the score moves with the "
        "conformation; on a predicted or modelled structure a low score is a "
        "statement about the model, not about whether the site can be drugged."
    ),
    "fpocket.single_conformation": (
        "Write the negative as 'no druggable pocket in this conformation of "
        "<structure>', naming the structure. One structure cannot support "
        "'this site cannot be drugged': three CDK2 crystal structures of the "
        "same, heavily drugged ATP site score 0.17, 0.29 and 0.94 on this "
        "scale. A score under the cutoff is a reason to score another "
        "conformation, not a reason to drop a target."
    ),
    "fpocket.druggability_is_not_affinity": (
        "Report this as 'the site has a pocket with drug-like geometry', never "
        "as evidence that a compound will bind or how tightly. The score "
        "describes the shape, volume and hydrophobicity of a cavity with no "
        "ligand in it; affinity is a property of a pair. A role that lacks an "
        "affinity tool is the one most likely to carry this number in its "
        "place — if that is why it is being read, the answer is that the "
        "question is untooled, not that the pocket is 0.94."
    ),
    "coscientist.partial_export": (
        "Confine conclusions to the ideas present in the export. Do not "
        "treat absence from it as evidence against an idea."
    ),
    "coscientist.review_recommendation_available": (
        "Address the co-scientist review recommendation before proceeding "
        "with target selection. The tournament produced a structured "
        "recommendation section — present it to the decision-maker and "
        "document whether the recommendation was followed, adapted, or "
        "rejected with rationale."
    ),
    "coscientist.leader_worst_contradiction_profile": (
        "The recommended idea has the highest contradicted-claim count "
        "among all candidates. The Science Lead must acknowledge this "
        "finding, justify proceeding with this target, and consider a "
        "fast-fail foundational claim check before committing a full cohort."
    ),
    "alphagenome.no_quantile_scores": (
        "Do not call any effect significant on raw score alone. No quantile "
        "was returned for the named output types, so the significance rule "
        "the interpretation guide defines could not be applied to them."
    ),
    "alphagenome.quantile_artifact": (
        "Exclude the flagged genes from the effect list entirely. An extreme "
        "quantile on a negligible raw score is a rank against a flat "
        "background; reporting it as a top-percentile effect inverts the "
        "finding."
    ),
    "alphagenome.band_modality_mismatch": (
        "State the assay the score came from alongside any magnitude word. "
        "The bands are RNA-seq-derived, so 'moderate' on a CAGE or ATAC "
        "track is an extrapolation, not a calibrated call."
    ),
    "alphagenome.effect_is_not_pathogenicity": (
        "Do not carry a predicted regulatory effect size into a clinical or "
        "pathogenicity claim. A large effect score means the model predicts "
        "this variant changes regulatory activity; it is not evidence of "
        "causality for a phenotype and not a clinical interpretation. Do "
        "not write 'pathogenic' — write 'predicted large regulatory effect.'"
    ),
    "expression.absent_is_not_evidence": (
        "Write the negative as 'not detected above the cutoff in HPA bulk "
        "consensus', naming the dataset. Do not write that the gene is absent "
        "from the tissue — one dataset cannot support that claim."
    ),
    # Named for what the finding must do, not for what the tool lacks.
    # `single_cell_available_unused` was the first name and it read as
    # "go and fetch it" — the one action nobody can take while
    # single-cell mode is unbuilt. A relay whose instruction cannot be
    # followed gets quoted and dropped.
    "expression.tissue_resolution_only": (
        "Scope the claim to whole-tissue averages and say so. Do not infer "
        "anything about a cell population from a tissue mean — HPA holds "
        "cell-resolved data for this gene that was not used, so the "
        "ecological fallacy here is live rather than hypothetical."
    ),
    "expression.single_cell_unavailable": (
        "State that no cell-resolved data exists for this gene, so the "
        "tissue-average verdict is the best obtainable and cannot be refined "
        "by fetching more. Do not leave the reader expecting a follow-up."
    ),
    "gnomad.constraint_unreliable": (
        "Report the constraint metric with its 90% confidence interval and say "
        "the gene could not be confidently categorised. Do not quote pLI or "
        "LOEUF bare, and do not resolve the ambiguity by picking the metric "
        "that agrees with the hypothesis."
    ),
    "gnomad.constraint_is_not_safety": (
        "Do not carry this into a safety or tolerability claim. LoF intolerance "
        "describes complete loss from conception across development; it says "
        "nothing about partial, reversible, adult pharmacological inhibition, "
        "and reading it as toxicology would eliminate most viable targets."
    ),
    "pubmed.search_not_exhaustive": (
        "A PubMed keyword search returns results matching the query terms but "
        "cannot guarantee exhaustive coverage. Relevant publications may use "
        "different terminology, be indexed under different MeSH headings, or "
        "not yet be indexed. Do not treat absence from search results as "
        "evidence of absence in the literature."
    ),
    "pubmed_bq.search_not_exhaustive": (
        "A PubMed BigQuery search uses SQL substring matching against article "
        "titles and abstracts. It cannot guarantee exhaustive coverage: relevant "
        "publications may use different terminology, alternate spellings, or "
        "synonyms not captured by the query terms. Do not treat absence from "
        "search results as evidence of absence in the literature."
    ),
    "litref.resolved_not_verified": (
        "Confirm the record says what the claim says before citing it. This "
        "tool established only that the record exists; it did not read it."
    ),
    "litref.ambiguous_name": (
        "Stop and obtain a unique identifier — NCT, PMID or DOI — before the "
        "claim proceeds. Do not choose among the candidates listed: they are "
        "listed because the tool refused to choose."
    ),
    "litref.name_match_not_unique_identifier": (
        "Report the match as 'one record found by title search', not as the "
        "record. A title search cannot see a name that appears only in an "
        "abstract or a trial description, so uniqueness is unproven."
    ),
    "compreg.resolved_not_verified": (
        "This tool confirmed the identifier maps to a real compound record and "
        "surfaced its canonical name and SMILES. It did not verify that the "
        "compound has the claimed biological activity, mechanism, or therapeutic "
        "indication -- those claims require separate evidence."
    ),
    "compreg.name_match_not_unique_identifier": (
        "This compound was matched by name, not by a unique registry identifier. "
        "A name search may miss synonyms and cannot prove uniqueness. Obtain the "
        "CID or ChEMBL ID and re-resolve."
    ),
    "expression.release_version_unknown": (
        "Cite the artifact by its payload SHA-256, not by an HPA release "
        "number. The release label was not obtained, so any version stated in "
        "the finding would be invented."
    ),
    "alphagenome.unexplained_missing_scores": (
        "Report the scored-track count, not the track total, and say that "
        "some tracks went unscored for reasons the tool could not explain. "
        "Do not aggregate over the full track set."
    ),
    "compound.fragment_stripped": (
        "Name the stripped fragments alongside any finding about the parent "
        "molecule.  The input was a multi-component SMILES (salt or mixture); "
        "descriptors and alerts were computed on the largest fragment only.  "
        "Do not attribute these properties to the original input without "
        "noting what was removed."
    ),
    "compound.alerts_not_toxicology": (
        "Do not conclude the compound is non-toxic from the absence of "
        "PAINS/Brenk/aggregator alerts.  These filters detect known assay "
        "interference patterns and undesirable substructures, not toxicity "
        "mechanisms.  A compound that passes them may still be toxic, "
        "reactive, or genotoxic by pathways these filters do not cover."
    ),
    "compound.sa_score_is_estimate": (
        "Do not conclude that a compound is synthetically feasible from a low "
        "SA-score alone. The SA-score is a computational estimate based on "
        "fragment frequency and molecular complexity -- it does not account for "
        "reagent availability, protecting group strategies, scalability, or "
        "specific reaction conditions. A low score means the molecule's "
        "substructures are commonly seen in known compounds, not that a "
        "synthesis route exists."
    ),
    "assay.screen_quality_insufficient": (
        "Do not report compound activity verdicts from this screen. The "
        "Z-factor is below the usability threshold (Zhang et al. 1999), "
        "meaning the assay window is too narrow to distinguish active from "
        "inactive compounds. The screen itself is the problem, not the "
        "compounds."
    ),
    "assay.cytotoxicity_confound": (
        "Flag this compound's dose-response as potentially confounded by "
        "cytotoxicity. A bell-shaped (non-monotonic) curve — where activity "
        "increases then decreases at higher concentrations — is a hallmark "
        "of cytotoxicity masking the primary pharmacological effect. Do not "
        "report the fitted IC50 without this caveat."
    ),
    "selectivity.ratio_not_affinity": (
        "Report selectivity ratios as assay-derived estimates, never as "
        "thermodynamic selectivity constants. A ratio computed from two IC50 "
        "values inherits whatever caveats apply to IC50 as a measure of "
        "affinity: IC50 is assay-dependent and not a thermodynamic binding "
        "constant, so a 50-fold ratio from two IC50 values each with 3-fold "
        "assay variability is not the same confidence as a ratio from Kd "
        "values. If this ratio is the basis for a selectivity claim, state "
        "the measure type and do not imply thermodynamic precision."
    ),
    "selectivity.panel_incomplete": (
        "Scope the selectivity claim to the off-targets that were actually "
        "tested, and name them. A selectivity assessment over a narrow panel "
        "is incomplete — testing 3 off-targets when a kinome-wide panel would "
        "be the real question. Do not generalise the selectivity claim beyond "
        "the tested panel."
    ),
    "gtex.whole_blood_is_not_peripheral_blood": (
        "Report this as 'GTEx whole-blood RNA-seq expression', not as "
        "'peripheral blood expression'. GTEx whole blood is drawn from "
        "femoral/subclavian veins and measured by bulk RNA-seq; it is a "
        "partial proxy for peripheral blood protein expression, not an "
        "equivalent measurement."
    ),
    "pk.rule_of_exponents_uncorrected": (
        "State that the fitted allometric exponent falls outside the simple "
        "allometry range (0.55–0.70) and that the Mahmood & Balian 1996 rule "
        "of exponents recommends a correction (MLP or brain weight) that has "
        "not been applied. The predicted human CL may be less reliable without "
        "this correction."
    ),
    "pk.single_species_scaling": (
        "Do not present this as a validated estimate. Single-species allometry has "
        "high uncertainty; the rule of exponents requires data from at least two "
        "species for reliable CL prediction. A human dose projection from one "
        "species should not be presented as a validated estimate."
    ),
    "screening.prefilter_excludes_not_rejects": (
        "Compounds excluded by the descriptor pre-filter were not docked, not "
        "proven inactive. The pre-filter is a compute-saving heuristic based on "
        "physicochemical property ranges; compounds outside these ranges may still "
        "bind the target. Report must state which pre-filter thresholds were "
        "applied and how many compounds were excluded."
    ),
    "docking.score_is_not_affinity": (
        "Report this as a predicted binding energy estimate, never as a measured "
        "affinity or a potency. A Vina score describes a computed interaction "
        "energy for a pose in a rigid pocket; binding affinity is a thermodynamic "
        "property of a pair measured in solution. A role that lacks an affinity "
        "assay tool is the one most likely to carry this number in its place — "
        "if that is why it is being read, the answer is that the question is "
        "untooled, not that the score is -8.2."
    ),
    "docking.contact_is_not_binding_event": (
        "State that the reported contacts represent geometric proximity "
        "within a static, computationally docked pose — not a confirmed "
        "binding interaction. No hydrogen-bond geometry, electrostatic "
        "complementarity, or reactive-orientation check was performed."
    ),
    "admet.prediction_not_measurement": (
        "Do not treat these predicted ADMET endpoints as measured values. Every "
        "number here is a rule-based prediction from molecular descriptors, not "
        "an in vitro measurement. A clean predicted ADMET profile does not "
        "substitute for in vitro ADMET studies — it identifies which studies to "
        "prioritize, not which to skip."
    ),
    "admet.herg_structural_flag": (
        "Name the specific structural features that triggered this hERG flag "
        "and state that it is a pharmacophore-based prediction, not a measured "
        "IC50 or patch-clamp result. Rule-based hERG prediction has a documented "
        "false-negative rate: absence of this flag is not evidence of hERG safety."
    ),
    "mmp.cliff_not_causation": (
        "Do not interpret a property cliff as evidence of a causal mechanism. "
        "A large property change across a single R-group transformation is a "
        "correlation in this dataset — it flags a substitution worth a medicinal "
        "chemist's attention, but it does not explain why the transformation "
        "matters mechanistically. The structural change may be incidental to "
        "the true driver."
    ),
    "mmp.small_pair_count": (
        "Do not report this transformation's property trend with confidence. "
        "The number of matched pairs supporting it is below the minimum "
        "required for a reliable SAR conclusion. Report the raw observation "
        "and the pair count, not a trend."
    ),
    "tox.genotox_weight_of_evidence": (
        "State that this genotoxicity assessment used ICH S2(R1) "
        "weight-of-evidence reasoning because the battery results were "
        "mixed. Do not report the verdict as a clean negative — name "
        "the positive assay and the basis for the overall assessment."
    ),
    "bioactivity.externally_sourced": (
        "State that these bioactivity values are literature-derived and retrieved "
        "from a public database. They are reported values from published assays, "
        "not measurements from this program's own screening. Do not present them "
        "as validated in-house data."
    ),
    "homology.structure_is_not_target": (
        "Do not describe this as the structure of the query protein. It is "
        "the structure of a homologous protein; any structural feature, "
        "binding site, or conformation attributed to the query protein from "
        "this structure is a hypothesis transferred by sequence similarity, "
        "not a direct observation. Name the source protein and the sequence "
        "identity in every structural claim."
    ),
    "gwas.association_not_causation": (
        "GWAS associations are statistical correlations between genetic variants "
        "and disease phenotypes. They do not establish causation, directionality, "
        "or mechanism. A significant association means the variant co-occurs with "
        "the phenotype more than expected by chance in the studied population -- "
        "it does not mean the gene product causes the disease or that modulating "
        "it will treat the disease."
    ),
    "clinvar.classification_is_curated": (
        "Do not describe ClinVar classifications as statistical associations or "
        "correlations. A ClinVar 'Pathogenic' call is a curated clinical "
        "assertion — expert reviewers assessed that this variant causes the "
        "named condition — not a p-value from a population study. However, "
        "variant-level pathogenicity does not imply the gene is a validated "
        "therapeutic target: pathogenicity describes what happens when the "
        "variant is present from conception, not what happens when the gene "
        "product is modulated pharmacologically in an adult."
    ),
    "clinvar.weak_review_status": (
        "Do not cite a ClinVar classification without its review status, and do "
        "not weight a classification with weak review status as though it were "
        "authoritative. A 'Pathogenic' call with 'no assertion criteria provided' "
        "carries much less weight than one 'reviewed by expert panel' or backed "
        "by a 'practice guideline'. Report both the classification and the review "
        "status, or withhold the classification."
    ),
    "ppi.interaction_not_functional": (
        "Protein-protein interactions reported by STRING are aggregated from "
        "multiple evidence channels including text mining, co-expression, and "
        "genomic context. A high interaction score does not confirm direct "
        "physical binding or functional relevance in the tissue or condition "
        "of interest. Experimental validation is required."
    ),
    "faers.spontaneous_reports_not_incidence": (
        "FAERS reports are spontaneous (voluntary) adverse event reports. They "
        "cannot establish incidence rates, causation, or comparative safety. "
        "Reporting rates are affected by media attention, time on market, "
        "indication severity, and reporter awareness. Do not interpret report "
        "counts as incidence or compare raw counts between drugs."
    ),
    "phenotype.model_organism_not_human": (
        "Phenotype data from model organisms (mouse, rat) may not translate "
        "directly to humans. Species differences in gene function, expression "
        "patterns, and compensatory mechanisms mean that a knockout phenotype "
        "in mouse is informative but not predictive of human clinical outcomes."
    ),
    "pathway.membership_not_activity": (
        "Pathway membership means the gene product is annotated to a pathway "
        "or GO term. It does not indicate that the gene is active, rate-limiting, "
        "or causally involved in the pathway in the tissue or condition of interest. "
        "Expression, activity, and essentiality are separate questions."
    ),
    "conservation.rate_is_not_function": (
        "Do not infer functional importance from conservation alone. A conserved "
        "residue evolves slowly across the sampled lineages; it may be "
        "structurally important, functionally important, or both, but conservation "
        "is a phylogenetic observation, not a functional annotation. A variable "
        "residue is not dispensable -- it may be under positive selection or "
        "lineage-specific constraint not captured by this alignment."
    ),
    "dice.in_vitro_not_in_vivo": (
        "DICE expression data is derived from in-vitro stimulated or sorted "
        "immune cells. Expression levels may differ from in-vivo tissue "
        "microenvironments. Cell isolation and culture conditions can alter "
        "gene expression profiles."
    ),
    "pubchem.annotation_is_not_validation": (
        "Published annotations (synonyms, MoA, pharmacological class) are database "
        "records, not in-house validation. Do not treat a PubChem pharmacological "
        "classification as equivalent to a verified mechanism study."
    ),
    "pubchem.drug_status_is_development_history": (
        "A ChEMBL max_phase of 4 means the compound has been approved for some "
        "indication in some jurisdiction. It does not confirm the compound is "
        "approved for the indication under study, or that it is still marketed."
    ),
    "similar.tanimoto_is_2d_only": (
        "Tanimoto similarity is computed from 2D fingerprints and reflects shared "
        "substructure topology, not 3D shape complementarity or biological activity. "
        "Two compounds with Tanimoto 0.95 may have very different binding modes, "
        "selectivity profiles, or ADMET properties."
    ),
    "cellxgene.search_is_metadata_only": (
        "CELLxGENE dataset search returns collection and dataset metadata; "
        "expression values require downloading the H5AD file or using the "
        "Census API. A dataset containing the queried tissue does not confirm "
        "expression of any specific gene."
    ),
    "geo.search_is_metadata_only": (
        "GEO dataset search returns dataset-level metadata (title, summary, "
        "sample list). It does not contain expression values or differential "
        "expression results. Determining whether a gene is differentially "
        "expressed in a dataset requires downloading and analysing the "
        "expression data (GEO2R, supplementary files, or SRA raw data)."
    ),
    "similar.database_coverage_limited": (
        "PubChem contains ~116M compounds and ChEMBL ~2.4M bioactive molecules. A "
        "compound with no similar hits may have close analogs in proprietary "
        "collections, patent literature, or databases not queried. Do not treat "
        "'novel by PubChem/ChEMBL' as 'novel.'"
    ),
    "scp.search_is_study_metadata": (
        "Single Cell Portal search returns study-level metadata; gene expression "
        "data requires authenticated access to individual studies. A matching "
        "study does not confirm expression of any specific gene."
    ),
    "disco.search_is_sample_metadata": (
        "DISCO search returns sample-level metadata (tissue, disease, cell "
        "count). Expression values and cell type markers are not included in "
        "search results. Confirming gene expression or cell type enrichment "
        "requires downloading the expression data (H5 files) from DISCO."
    ),
    "spatialdb.spatial_not_bulk": (
        "SpatialDB records describe spatially resolved expression experiments, "
        "not bulk or single-cell RNA-seq. Spatial transcriptomics captures gene "
        "expression with tissue coordinates but covers a limited set of tissues "
        "and studies. Absence from SpatialDB does not mean a gene lacks spatial "
        "expression data — the database indexes published spatial transcriptomics "
        "datasets, not all spatial experiments."
    ),
    "disignatlas.curated_signatures": (
        "Do not treat DisigNAtlas signatures as primary experimental evidence. "
        "These are pre-computed differential expression results aggregated from "
        "public datasets (GEO, ArrayExpress, TCGA) using standardised pipelines. "
        "Individual study quality, sample sizes, and normalisation methods vary. "
        "Treat as a discovery resource for identifying disease-gene associations, "
        "not as a substitute for primary analysis of the underlying data."
    ),
    "trials.active_competitor_pipeline": (
        "Report that active Phase 3+ clinical trials exist for this target or "
        "query. Late-stage clinical development may affect freedom to operate or "
        "competitive positioning. Name the specific trials, sponsors, and "
        "indications. Do not treat the presence of competitor trials as evidence "
        "that the target is validated — a trial is a bet, not a result."
    ),
    "patent.fto_risk_identified": (
        "Patent landscape shows recent filings that may affect freedom to "
        "operate. Check assignees, claim scope, and jurisdiction before "
        "proceeding."
    ),
    "cite.phantom_citation": (
        "Name the phantom references. A phantom citation invalidates the "
        "claim resting on it, not merely the reference."
    ),
    "cite.suspect_title_match": (
        "State that the reference resolved but the title did not match "
        "within tolerance. Do not report it as verified."
    ),
    "cite.unresolved_offline": (
        "Confine the verification claim to references that resolved. Do "
        "not extend the conclusion to references that could not be checked."
    ),
    "cite.extraction_incomplete": (
        "State that references were recovered by pattern match. A reference "
        "the extractor missed is not in the manifest and was not checked."
    ),
    "preprint.no_results": (
        "The preprint search returned no results. Consider broadening the "
        "query or checking alternative sources."
    ),
    "preprint.query_truncated": (
        "Results were capped at the requested maximum. Additional matching "
        "preprints may exist."
    ),
    # --- cBioPortal ---
    "cbioportal.no_results": (
        "The cBioPortal search returned no results. Consider broadening "
        "the query or checking alternative cancer genomics databases."
    ),
    "cbioportal.query_truncated": (
        "Results were capped at the requested maximum. Additional matching "
        "studies may exist."
    ),
    # --- hypothesis adoption ---
    "hypothesis.adopted_not_generated": (
        "The hypothesis set was attested by a human, not retrieved by a "
        "tool. Its provenance chain terminates at the attestation. Quote "
        "the attestation verbatim in any finding that rests on this "
        "artifact, and do not describe the set as DDE-derived."
    ),
    "hypothesis.unranked_set": (
        "This set carries no ranking. Array position is input order, not "
        "preference. Do not present it as a leaderboard or select 'the "
        "top candidate' from it."
    ),
}


def relay(code: str, message: str) -> dict[str, str]:
    """Build a mandatory-relay record, rejecting unregistered codes."""
    if code not in RELAY_CODES:
        raise KeyError(
            f"unregistered relay code {code!r}; add it to provenance.RELAY_CODES "
            "so skills and reviewers can enumerate it"
        )
    return {"code": code, "message": message}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Sidecar:
    """Builder for a phase-1 `.meta.json` provenance record."""

    def __init__(
        self,
        tool: str,
        subcommand: str,
        endpoint: str | None = None,
        parameters: dict[str, Any] | None = None,
    ):
        self.tool = tool
        self.subcommand = subcommand
        self.endpoint = endpoint
        self.parameters = parameters or {}
        self.outputs: list[dict[str, Any]] = []
        self.warnings: list[str] = list(env.env_warnings())
        self.relays: list[dict[str, str]] = []
        self.extra: dict[str, Any] = {}
        self.started = _utc_now()

    def add_output(self, path: Path) -> Path:
        path = Path(path)
        if not path.is_file():
            raise ArtifactError(f"declared output does not exist: {path}")
        self.outputs.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
        return path

    def warn(self, message: str, code: str | None = None) -> None:
        """Record a warning; with `code`, also mark it a mandatory relay.

        Relays stay in `warnings` too. A reader that knows nothing about
        codes still sees the prose, so adding this could not quietly
        remove a warning from anyone's view.
        """
        if message not in self.warnings:
            self.warnings.append(message)
        if code and not any(r["code"] == code for r in self.relays):
            self.relays.append(relay(code, message))

    def note(self, key: str, value: Any) -> None:
        self.extra[key] = value

    def to_dict(self) -> dict[str, Any]:
        record = {
            "tool": self.tool,
            "subcommand": self.subcommand,
            "work_order_id": _work_order(),
            "cli_version": env.CLI_VERSION,
            "env_version": env.env_version(),
            "interpreter": env.interpreter_tag(),
            "endpoint": self.endpoint,
            "parameters": self.parameters,
            "timestamp": self.started,
            "completed": _utc_now(),
            "outputs": self.outputs,
            "warnings": self.warnings,
            "mandatory_relays": self.relays,
        }
        record.update(self.extra)
        return record

    def write(self, path: Path) -> Path:
        """Write the sidecar. `path` is the full .meta.json path."""
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path


def write_analysis(
    path: Path,
    source: str,
    threshold_set: str,
    thresholds_applied: dict[str, Any],
    metrics: dict[str, Any],
    assessment: dict[str, Any],
    *,
    threshold_sources: dict[str, str] | None = None,
    threshold_provenance: str | None = None,
    unresolved: list[str] | None = None,
    mandatory_relays: list[dict[str, str]] | None = None,
    suppress_warnings: bool = False,
) -> Path:
    """Write a phase-2 `.analysis.json` record.

    `mandatory_relays` is promoted to a top-level field rather than being
    buried in `assessment`, because it is what a reviewer checks against
    the Layer 1 finding. It should be reachable without knowing the
    shape of any particular tool's assessment.

    **Refuses to replace a differing analysis.** A second opinion must be
    producible without destroying the first one, and a path scheme is a
    weak way to guarantee that: two reviewers auditing the same artifact
    on the same day resolve to the same `raw/reanalysis/<date>/` file,
    and the later write silently becomes the record. So the guarantee
    lives at the write instead, where it holds whatever the path scheme
    turns out to be. Re-running and getting the same verdict is not a
    conflict and passes silently; getting a different one raises, because
    that is precisely the case where somebody's citation is about to stop
    matching the file it cites.
    """
    record: dict[str, Any] = {
        "source": source,
        "cli_version": env.CLI_VERSION,
        "env_version": env.env_version(),
        "timestamp": _utc_now(),
        "threshold_set": threshold_set,
        "thresholds_applied": thresholds_applied,
        "metrics": metrics,
        "assessment": assessment,
    }
    if mandatory_relays:
        record["mandatory_relays"] = mandatory_relays
    if threshold_sources:
        record["threshold_sources"] = threshold_sources
    if threshold_provenance:
        record["threshold_provenance"] = threshold_provenance
    if unresolved:
        record["thresholds_unresolved"] = unresolved
    # Always present, even when nothing identifies the caller. A field
    # that is sometimes absent cannot be quoted by a reviewer's report,
    # and a check that degrades to "the field was missing" is not a
    # check. "unattributed" is quotable and true, and reads as the
    # finding it is when it turns up in an audit.
    record["written_by"] = _writer() or "unattributed"
    record["work_order_id"] = _work_order()

    # The digest of the artifact this verdict was computed from. Makes a
    # reviewer's citation exact rather than approximate: two records that
    # name the same source path but different source digests analysed
    # different bytes, and that is the fact worth having at the moment
    # two conclusions disagree.
    source_digest = _source_digest(source)
    if source_digest:
        record["source_sha256"] = source_digest

    path = Path(path)
    if _may_write(path, record, suppress_warnings=suppress_warnings):
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _source_digest(source: str) -> str | None:
    """sha256 of the source artifact, when `source` names a readable file.

    `source` is a free-text provenance string on some commands (an
    accession, an endpoint) and a path on others. Hash it when it is a
    file and stay quiet when it is not — a digest invented for a
    non-file would be worse than no digest.
    """
    try:
        candidate = Path(source)
        if candidate.is_file():
            return sha256_file(candidate)
    except (OSError, ValueError):
        pass
    return None


def _comparable(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _VOLATILE_ANALYSIS_FIELDS}


def _may_write(path: Path, record: dict[str, Any], *, suppress_warnings: bool = False) -> bool:
    """Decide whether this analysis may land at this path.

    Three outcomes, and the middle one is the reason this is a decision
    and not an assertion:

    * nothing there, or `--overwrite` — write.
    * a record that agrees — **keep the one already there**, return
      False, and say so on stderr.
    * anything else, including a file that cannot be read — refuse
      (exit 9).

    The middle case used to be a silent overwrite, because the guard
    answered "is this a conflict?" and its caller read the answer as
    "may I write?". Those two questions agree everywhere except on the
    fields excluded from the comparison — so they diverged exactly where
    nobody was looking, and `written_by` is an excluded field. A
    reviewer who re-ran without `--out` and *agreed* with the specialist
    silently took authorship of the specialist's record, at exit 0. The
    disagreeing case was loud and the agreeing case was the common one.
    (Repro from template-builder; diagnosis from plan-review, whose
    generalisation is now orchestration-design-guidance §8.0: a field
    excluded from an equality test is a field that can change without
    anyone noticing, so exclude it only if losing it costs nothing.)

    Rewriting an identical record buys a fresher timestamp and pays with
    the first author's name. Not writing keeps both the attribution and,
    just as usefully, the bytes: a reviewer's confirming run now leaves
    `raw/` untouched, so a checksum sweep over the evidence still reads
    clean afterwards. That is why this is (a) — do not write — rather
    than appending the confirming agent to the record. Appending would
    preserve more, but it would also mutate the artifact under audit,
    and an integrity sweep cannot tell a benign append from tampering.

    An unreadable or non-conforming file is treated as a conflict. It
    cannot be shown to agree, and "could not check" must not resolve to
    "went ahead" — that is the shape of every fabrication this
    architecture is built to prevent.
    """
    if _overwrite_allowed or not path.exists():
        return True
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None

    if isinstance(existing, dict) and _comparable(existing) == _comparable(record):
        if not suppress_warnings:
            previous = existing.get("written_by")
            when = existing.get("timestamp") or "an earlier run"
            mine = record.get("written_by")
            if previous and mine and previous != mine:
                output.warn(
                    f"{path.name} already holds an identical record by {previous} "
                    f"({when}); left as it is. Your run confirms it — cite "
                    f"{previous}'s record, and pass --out if you need your own copy."
                )
            else:
                output.warn(
                    f"{path.name} already holds an identical record ({when}); "
                    "not rewritten."
                )
        return False

    if isinstance(existing, dict):
        previous = existing.get("written_by") or "an earlier run"
        when = existing.get("timestamp") or "unknown time"
        detail = f"the existing record was written by {previous} at {when}"
    else:
        detail = "the existing file could not be read as an analysis record"

    raise Refusal(
        f"{path.name} already holds a different analysis",
        detail=detail,
        remedy=(
            "write this verdict somewhere else with `--out "
            "raw/reanalysis/<date>-<agent>` and compare the two, or pass "
            "`--overwrite` if replacing the earlier record is what you mean. "
            "Two verdicts that disagree are evidence; one of them silently "
            "replaced is not"
        ),
    )


def read_json(path: Path, what: str = "artifact") -> Any:
    """Read a JSON artifact, failing loudly and specifically."""
    path = Path(path)
    if not path.is_file():
        raise ArtifactError(
            f"{what} not found: {path}",
            remedy="run the corresponding phase-1 subcommand first",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{what} is not valid JSON: {path}", detail=str(exc))
