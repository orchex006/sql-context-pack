"""Deterministic relationship and graph-ready index construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlctx.classification.classifier import ClassificationRun
from sqlctx.core.enums import ClassificationPass, ClassificationStatus, ConstraintType, EdgeType
from sqlctx.core.models import CatalogSnapshot, DatabaseObject, MaterializationPlan


@dataclass(frozen=True)
class IndexBundle:
    objects: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    routine_dependencies: list[dict[str, Any]]
    tags: list[dict[str, Any]]
    graph: dict[str, Any]


def _has_unique_key(obj: DatabaseObject, columns: list[str]) -> bool:
    wanted = {item.lower() for item in columns}
    return any(
        constraint.constraint_type in {ConstraintType.PRIMARY_KEY, ConstraintType.UNIQUE}
        and {item.lower() for item in constraint.columns} == wanted
        for constraint in obj.constraints
    )


class IndexBuilder:
    EDGE_NAMES = {
        EdgeType.FOREIGN_KEY: "FOREIGN_KEY",
        EdgeType.ROUTINE_READ: "READS_FROM",
        EdgeType.ROUTINE_WRITE: "WRITES_TO",
        EdgeType.ROUTINE_CALL: "CALLS",
        EdgeType.BOUNDARY: "REFERENCES",
    }

    def build(
        self,
        snapshot: CatalogSnapshot,
        classifications: ClassificationRun,
        plan: MaterializationPlan,
    ) -> IndexBundle:
        final = {
            result.object_id: result.category
            for result in classifications.results
            if result.pass_name == ClassificationPass.PASS_2
            and result.status == ClassificationStatus.FINAL_CONFIRMED
        }
        included = {item.object_id for item in plan.items if item.included}
        objects_by_id = {item.ref.object_id: item for item in snapshot.objects}
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        object_index: list[dict[str, Any]] = []
        tags: list[dict[str, Any]] = []

        database_id = f"database:{snapshot.profile_name}"
        nodes[database_id] = {"id": database_id, "type": "database", "name": snapshot.profile_name}
        for obj in sorted(snapshot.objects, key=lambda item: item.ref.object_id):
            ref = obj.ref
            schema_id = f"schema:{ref.schema_name}"
            nodes.setdefault(
                schema_id, {"id": schema_id, "type": "schema", "name": ref.schema_name}
            )
            edges.append({"source": database_id, "target": schema_id, "type": "CONTAINS"})
            category = final.get(ref.object_id)
            if category:
                category_id = f"category:{category}"
                nodes.setdefault(
                    category_id, {"id": category_id, "type": "category", "name": category}
                )
                edges.append(
                    {"source": ref.object_id, "target": category_id, "type": "BELONGS_TO_CATEGORY"}
                )
            nodes[ref.object_id] = {
                "id": ref.object_id,
                "type": ref.object_type,
                "name": ref.object_name,
                "schema": ref.schema_name,
                "materialized": ref.object_id in included,
            }
            edges.append({"source": schema_id, "target": ref.object_id, "type": "CONTAINS"})
            object_index.append(
                {
                    "object_id": ref.object_id,
                    "schema": ref.schema_name,
                    "name": ref.object_name,
                    "object_type": ref.object_type,
                    "category": category,
                    "materialized": ref.object_id in included,
                    "content_hash": obj.source_fingerprint,
                }
            )
            tags.append(
                {
                    "object_id": ref.object_id,
                    "tags": [value for value in [category, ref.object_type] if value],
                }
            )
            primary_columns = {
                column
                for constraint in obj.constraints
                if constraint.constraint_type == ConstraintType.PRIMARY_KEY
                for column in constraint.columns
            }
            for column in sorted(obj.columns, key=lambda item: item.ordinal):
                column_id = f"{ref.object_id}:column:{column.name}"
                nodes[column_id] = {
                    "id": column_id,
                    "type": "column",
                    "name": column.name,
                    "data_type": column.data_type,
                }
                edges.append({"source": ref.object_id, "target": column_id, "type": "HAS_COLUMN"})
                if column.name in primary_columns:
                    edges.append(
                        {"source": ref.object_id, "target": column_id, "type": "PRIMARY_KEY"}
                    )

        relationships: list[dict[str, Any]] = []
        for source in sorted(snapshot.objects, key=lambda item: item.ref.object_id):
            associative = len(source.foreign_keys) == 2 and any(
                constraint.constraint_type == ConstraintType.PRIMARY_KEY
                and set(constraint.columns)
                == {column for fk in source.foreign_keys for column in fk.source_columns}
                for constraint in source.constraints
            )
            for fk in source.foreign_keys:
                resolved = fk.target_object_id in objects_by_id
                cardinality = (
                    "M:N"
                    if associative
                    else "1:1"
                    if _has_unique_key(source, fk.source_columns)
                    else "1:N"
                )
                boundary = (source.ref.object_id in included) != (fk.target_object_id in included)
                relationship = {
                    "name": fk.name,
                    "source": source.ref.object_id,
                    "target": fk.target_object_id,
                    "source_columns": fk.source_columns,
                    "target_columns": fk.target_columns,
                    "cardinality": cardinality,
                    "confidence": "confirmed" if resolved else "unknown",
                    "boundary_only": boundary,
                    "target_resolved": resolved,
                }
                relationships.append(relationship)
                edges.append(
                    {
                        "source": source.ref.object_id,
                        "target": fk.target_object_id,
                        "type": "FOREIGN_KEY",
                        "boundary_only": boundary,
                    }
                )

        routine_dependencies: list[dict[str, Any]] = []
        for dependency in sorted(
            snapshot.dependencies,
            key=lambda item: (item.source_object_id, item.target_object_id, item.edge_type),
        ):
            if dependency.edge_type == EdgeType.FOREIGN_KEY:
                continue
            entry = dependency.model_dump(mode="json")
            entry["target_resolved"] = dependency.target_object_id in objects_by_id
            routine_dependencies.append(entry)
            edges.append(
                {
                    "source": dependency.source_object_id,
                    "target": dependency.target_object_id,
                    "type": self.EDGE_NAMES[dependency.edge_type],
                    "boundary_only": (dependency.source_object_id in included)
                    != (dependency.target_object_id in included),
                }
            )

        unique_edges = {
            (
                item["source"],
                item["target"],
                item["type"],
                bool(item.get("boundary_only", False)),
            ): item
            for item in edges
        }
        edge_list = [unique_edges[key] for key in sorted(unique_edges)]
        node_list = [nodes[key] for key in sorted(nodes)]
        graph = {"directed": True, "nodes": node_list, "edges": edge_list}
        return IndexBundle(
            objects=object_index,
            nodes=node_list,
            edges=edge_list,
            relationships=relationships,
            routine_dependencies=routine_dependencies,
            tags=tags,
            graph=graph,
        )
