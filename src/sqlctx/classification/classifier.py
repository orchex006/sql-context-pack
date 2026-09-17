"""Two-pass deterministic classification with non-authoritative proposal intake."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sqlctx.application.catalog import CatalogService
from sqlctx.application.pagination import page_slice
from sqlctx.classification.rules import CategoryConfig, CategoryRuleRepository
from sqlctx.core.enums import (
    ClassificationPass,
    ClassificationStatus,
    EdgeType,
    InclusionReason,
    MaterializationMode,
)
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    CatalogSnapshot,
    ClassificationCandidate,
    ClassificationChange,
    ClassificationPassResult,
    ClassificationProposalBatch,
    ClassificationRequest,
    ClassificationRequestPage,
    DatabaseObject,
    EvidenceRecord,
    MaterializationPlan,
    MaterializationPlanItem,
    MaterializationSelection,
    ProposalBatchResult,
)
from sqlctx.security.approvals import ApprovalService
from sqlctx.security.runtime import JsonRuntimeStateStore


class ClassificationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: str
    results: list[ClassificationPassResult]
    evidence: list[EvidenceRecord]
    changes: list[ClassificationChange]
    categories: list[str]


class StoredProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    harness: str
    skill_version: str


@dataclass(frozen=True)
class _Match:
    category: str
    confidence: float
    kind: str
    summary: str
    authoritative: bool = False


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", value.lower()) if len(part) > 1}


class ClassificationService:
    def __init__(
        self,
        state: JsonRuntimeStateStore,
        catalogs: CatalogService,
        rules: CategoryRuleRepository,
        approvals: ApprovalService,
    ) -> None:
        self.state = state
        self.catalogs = catalogs
        self.rules = rules
        self.approvals = approvals

    @staticmethod
    def _evidence(object_id: str, match: _Match) -> EvidenceRecord:
        payload = f"{object_id}\0{match.kind}\0{match.category}\0{match.summary}".encode()
        evidence_id = "ev_" + hashlib.sha256(payload).hexdigest()[:24]
        return EvidenceRecord(
            evidence_id=evidence_id,
            object_id=object_id,
            kind=match.kind,
            summary=match.summary[:240],
        )

    @staticmethod
    def _pass_1_matches(obj: DatabaseObject, rules: CategoryConfig) -> list[_Match]:
        name = obj.ref.object_name.upper()
        schema = obj.ref.schema_name.lower()
        comment = (obj.native_comment or "").lower()
        matches: list[_Match] = []
        for rule in rules.categories:
            if name in {item.upper() for item in rule.exact_names}:
                matches.append(
                    _Match(rule.name, 1.0, "configured_exact", f"exact name {name}", True)
                )
            elif any(name.startswith(prefix.upper()) for prefix in rule.prefixes):
                matches.append(
                    _Match(
                        rule.name, 0.98, "configured_prefix", f"configured prefix for {name}", True
                    )
                )
            elif schema in {item.lower() for item in rule.schemas}:
                matches.append(
                    _Match(
                        rule.name, 0.95, "configured_schema", f"configured schema {schema}", True
                    )
                )
            elif any(keyword.lower() in comment for keyword in rule.comment_keywords):
                matches.append(_Match(rule.name, 0.72, "comment", "sanitized native comment"))
        return matches

    @staticmethod
    def _context_matches(
        obj: DatabaseObject,
        snapshot: CatalogSnapshot,
        rules: CategoryConfig,
        pass_1: dict[str, str | None],
    ) -> list[_Match]:
        matches: list[_Match] = []
        column_tokens = {token for column in obj.columns for token in _tokens(column.name)}
        name_tokens = _tokens(obj.ref.object_name)
        for rule in rules.categories:
            keywords = {token for value in rule.column_keywords for token in _tokens(value)}
            overlap = sorted(column_tokens & keywords)
            if overlap:
                matches.append(
                    _Match(
                        rule.name,
                        min(0.88, 0.65 + 0.05 * len(overlap)),
                        "columns",
                        "column shape: " + ", ".join(overlap),
                    )
                )
            rule_tokens = _tokens(rule.name + " " + rule.description)
            similarity = len(name_tokens & rule_tokens) / max(1, len(name_tokens | rule_tokens))
            if similarity >= 0.25:
                matches.append(
                    _Match(
                        rule.name,
                        min(0.70, 0.50 + similarity / 2),
                        "token_similarity",
                        "name token similarity",
                    )
                )
        neighbors: list[str] = []
        for edge in snapshot.dependencies:
            if edge.source_object_id == obj.ref.object_id:
                neighbors.append(edge.target_object_id)
            elif edge.target_object_id == obj.ref.object_id:
                neighbors.append(edge.source_object_id)
        categories = [category for item in neighbors if (category := pass_1.get(item)) is not None]
        if categories and len(set(categories)) == 1:
            edge_kinds = {
                edge.edge_type
                for edge in snapshot.dependencies
                if obj.ref.object_id in {edge.source_object_id, edge.target_object_id}
            }
            kind = (
                "routine_dependency"
                if edge_kinds
                & {EdgeType.ROUTINE_READ, EdgeType.ROUTINE_WRITE, EdgeType.ROUTINE_CALL}
                else "foreign_key_neighborhood"
            )
            matches.append(
                _Match(categories[0], 0.80, kind, "single-category dependency neighborhood")
            )
        sample = snapshot.samples.get(obj.ref.object_id)
        if sample:
            sample_columns = " ".join(sample.columns).lower()
            for rule in rules.categories:
                if any(keyword.lower() in sample_columns for keyword in rule.column_keywords):
                    matches.append(
                        _Match(rule.name, 0.68, "sample_shape", "sanitized sample column shape")
                    )
        return matches

    @staticmethod
    def _pick(
        matches: list[_Match], *, allow_confirmed: bool
    ) -> tuple[str | None, ClassificationStatus, list[_Match]]:
        if not matches:
            return None, ClassificationStatus.FINAL_UNRESOLVED, []
        by_category: dict[str, _Match] = {}
        for match in matches:
            previous = by_category.get(match.category)
            if previous is None or match.confidence > previous.confidence:
                by_category[match.category] = match
        ranked = sorted(by_category.values(), key=lambda item: (-item.confidence, item.category))
        authoritative = [item for item in ranked if item.authoritative]
        if allow_confirmed and authoritative:
            authoritative_categories = {item.category for item in authoritative}
            if len(authoritative_categories) == 1:
                return authoritative[0].category, ClassificationStatus.FINAL_CONFIRMED, ranked
            return None, ClassificationStatus.FINAL_UNRESOLVED, ranked
        if len(ranked) > 1 and ranked[0].confidence - ranked[1].confidence < 0.15:
            return None, ClassificationStatus.FINAL_UNRESOLVED, ranked
        best = ranked[0]
        status = (
            ClassificationStatus.FINAL_CONFIRMED
            if allow_confirmed and best.authoritative
            else ClassificationStatus.FINAL_SUGGESTED
        )
        return best.category, status, ranked

    def classify(
        self,
        catalog_id: str,
        selection: MaterializationSelection | None = None,
    ) -> ClassificationRun:
        snapshot = self.catalogs.get_snapshot(catalog_id)
        rules, overrides = self.rules.load()
        proposals = [
            StoredProposal.model_validate(item)
            for item in self.state.read_json(f"catalogs/{catalog_id}/proposals.json", [])
        ]
        proposals_by_object: dict[str, list[StoredProposal]] = {}
        for proposal_record in proposals:
            proposals_by_object.setdefault(proposal_record.object_id, []).append(proposal_record)
        results: list[ClassificationPassResult] = []
        evidence: dict[str, EvidenceRecord] = {}
        preliminary: dict[str, str | None] = {}

        for obj in snapshot.objects:
            matches = self._pass_1_matches(obj, rules)
            category, _, ranked = self._pick(matches, allow_confirmed=False)
            preliminary[obj.ref.object_id] = category
            candidates = []
            for match in ranked:
                evidence_record = self._evidence(obj.ref.object_id, match)
                evidence[evidence_record.evidence_id] = evidence_record
                candidates.append(
                    ClassificationCandidate(
                        category=match.category,
                        confidence=match.confidence,
                        evidence_ids=[evidence_record.evidence_id],
                    )
                )
            results.append(
                ClassificationPassResult(
                    object_id=obj.ref.object_id,
                    pass_name=ClassificationPass.PASS_1,
                    status=ClassificationStatus.PRELIMINARY,
                    category=category,
                    candidates=candidates,
                )
            )

        changes: list[ClassificationChange] = []
        selected = set(selection.selected_categories) if selection else set()
        for obj in snapshot.objects:
            object_id = obj.ref.object_id
            contextual = self._pass_1_matches(obj, rules) + self._context_matches(
                obj, snapshot, rules, preliminary
            )
            override = overrides.overrides.get(object_id)
            if override:
                contextual.insert(
                    0,
                    _Match(
                        override, 1.0, "owner_override", "owner-approved persistent override", True
                    ),
                )
            for proposal in proposals_by_object.get(object_id, []):
                contextual.append(
                    _Match(
                        proposal.category,
                        proposal.confidence,
                        "model_proposal",
                        f"sanitized proposal from {proposal.harness}/{proposal.skill_version}",
                    )
                )
            category, status, ranked = self._pick(contextual, allow_confirmed=True)
            if override:
                category, status = override, ClassificationStatus.FINAL_CONFIRMED
            candidates = []
            for match in ranked:
                evidence_record = self._evidence(object_id, match)
                evidence[evidence_record.evidence_id] = evidence_record
                candidates.append(
                    ClassificationCandidate(
                        category=match.category,
                        confidence=match.confidence,
                        evidence_ids=[evidence_record.evidence_id],
                    )
                )
            results.append(
                ClassificationPassResult(
                    object_id=object_id,
                    pass_name=ClassificationPass.PASS_2,
                    status=status,
                    category=category,
                    candidates=candidates,
                )
            )
            before = preliminary[object_id]
            changes.append(
                ClassificationChange(
                    object_id=object_id,
                    pass_1_category=before,
                    pass_2_category=category,
                    moved_into_selected=bool(
                        selected and before not in selected and category in selected
                    ),
                    moved_out_of_selected=bool(
                        selected and before in selected and category not in selected
                    ),
                )
            )

        run = ClassificationRun(
            catalog_id=catalog_id,
            results=results,
            evidence=list(evidence.values()),
            changes=changes,
            categories=sorted(rules.names),
        )
        self.state.write_json(
            f"catalogs/{catalog_id}/classification.json", run.model_dump(mode="json")
        )
        self.catalogs.save_classifications(catalog_id, results)
        final_lut_ids = [
            item.object_id
            for item in results
            if item.pass_name == ClassificationPass.PASS_2 and item.category == "lut"
        ]
        refresh_lut_samples = getattr(self.catalogs, "refresh_lut_samples", None)
        failed_lut_ids = (
            refresh_lut_samples(catalog_id, final_lut_ids) if callable(refresh_lut_samples) else []
        )
        if failed_lut_ids:
            self.state.write_json(
                f"catalogs/{catalog_id}/lut-refresh-warnings.json",
                {"failed_object_ids": failed_lut_ids},
            )
        return run

    def intake_proposals(
        self, catalog_id: str, batch: ClassificationProposalBatch
    ) -> ProposalBatchResult:
        run = self.get_run(catalog_id)
        known_objects = {
            item.ref.object_id for item in self.catalogs.get_snapshot(catalog_id).objects
        }
        known_evidence = {item.evidence_id: item.object_id for item in run.evidence}
        accepted: list[StoredProposal] = []
        rejected = 0
        for proposal in batch.proposals:
            if (
                proposal.object_id not in known_objects
                or proposal.category not in set(run.categories)
                or any(
                    known_evidence.get(evidence_id) != proposal.object_id
                    for evidence_id in proposal.evidence_ids
                )
            ):
                rejected += 1
                continue
            accepted.append(
                StoredProposal(
                    **proposal.model_dump(),
                    harness=batch.harness,
                    skill_version=batch.skill_version,
                )
            )
        previous = self.state.read_json(f"catalogs/{catalog_id}/proposals.json", [])
        self.state.write_json(
            f"catalogs/{catalog_id}/proposals.json",
            [*previous, *[item.model_dump(mode="json") for item in accepted]],
        )
        return ProposalBatchResult(
            accepted_as_suggestion=len(accepted),
            rejected=rejected,
            requires_owner_resolution=len(accepted),
        )

    def requests(
        self, catalog_id: str, *, cursor: str | None = None, limit: int = 100
    ) -> ClassificationRequestPage:
        run = self.get_run(catalog_id)
        evidence = {item.evidence_id: item.summary for item in run.evidence}
        unresolved = [
            item
            for item in run.results
            if item.pass_name == ClassificationPass.PASS_2
            and item.status != ClassificationStatus.FINAL_CONFIRMED
        ]
        all_items = [
            ClassificationRequest(
                request_id="clr_" + hashlib.sha256(item.object_id.encode()).hexdigest()[:20],
                object_id=item.object_id,
                current_categories=run.categories,
                sanitized_evidence=[
                    evidence[eid]
                    for candidate in item.candidates
                    for eid in candidate.evidence_ids
                    if eid in evidence
                ],
                candidates=item.candidates,
            )
            for item in unresolved
        ]
        items, page = page_slice(all_items, cursor=cursor, limit=limit)
        return ClassificationRequestPage(items=items, page=page)

    def materialization_plan(self, catalog_id: str) -> MaterializationPlan:
        run = self.get_run(catalog_id)
        selection = self.catalogs.materialization_plan(catalog_id).selection
        final = {
            item.object_id: item.category
            for item in run.results
            if item.pass_name == ClassificationPass.PASS_2
            and item.status == ClassificationStatus.FINAL_CONFIRMED
        }
        items: list[MaterializationPlanItem] = []
        for obj in self.catalogs.get_snapshot(catalog_id).objects:
            category = final.get(obj.ref.object_id)
            included = selection.mode == MaterializationMode.ALL or (
                category is not None
                and (
                    (selection.mode != MaterializationMode.ASK and category == "lut")
                    or (
                        selection.mode == MaterializationMode.SELECTED
                        and category in selection.selected_categories
                    )
                )
            )
            items.append(
                MaterializationPlanItem(
                    object_id=obj.ref.object_id,
                    final_category=category,
                    included=included,
                    reason=(
                        InclusionReason.ALL_MODE
                        if selection.mode == MaterializationMode.ALL
                        else InclusionReason.POLICY_ALWAYS_INCLUDE
                        if included and category == "lut"
                        else InclusionReason.SELECTED_CATEGORY
                        if included
                        else InclusionReason.INTENTIONALLY_EXCLUDED
                    ),
                )
            )
        return MaterializationPlan(catalog_id=catalog_id, selection=selection, items=items)

    def resolve(
        self, catalog_id: str, *, object_id: str, category: str, caller: str, approval_id: str
    ) -> ClassificationRun:
        payload = {
            "catalog_id": catalog_id,
            "object_id": object_id,
            "category": category,
            "persist_as_owner_override": True,
        }
        self.approvals.consume(
            approval_id,
            caller=caller,
            operation="classification.resolve",
            target=object_id,
            payload=payload,
        )
        if object_id not in {
            item.ref.object_id for item in self.catalogs.get_snapshot(catalog_id).objects
        }:
            raise SqlCtxError("UNKNOWN_OBJECT", "Owner resolution references an unknown object.")
        self.rules.save_override(object_id, category)
        return self.classify(catalog_id)

    def resolve_authorized(self, catalog_id: str, resolutions: dict[str, str]) -> ClassificationRun:
        known = {item.ref.object_id for item in self.catalogs.get_snapshot(catalog_id).objects}
        if not set(resolutions) <= known:
            raise SqlCtxError("UNKNOWN_OBJECT", "Owner resolution references an unknown object.")
        for object_id, category in resolutions.items():
            self.rules.save_override(object_id, category)
        return self.classify(catalog_id)

    def get_run(self, catalog_id: str) -> ClassificationRun:
        value: Any = self.state.read_json(f"catalogs/{catalog_id}/classification.json")
        if value is None:
            return self.classify(catalog_id)
        return ClassificationRun.model_validate(value)
