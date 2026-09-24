"""Additive, ownership-safe reconciliation for Twenty workspace metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Any

from malg.crm.client import TwentyClient

MANIFEST_RESOURCE = files("malg.crm").joinpath("contract.json")
MANAGED_DESCRIPTION = "Managed by MALG contract"
_OBJECTS_QUERY = """
query Objects($after: ConnectionCursor) {
  objects(paging: { first: 500, after: $after }, filter: {}) {
    edges {
      node {
        id
        nameSingular
        namePlural
        labelSingular
        labelPlural
        description
        fieldsList {
          id name label type description isNullable isUnique settings
          relation {
            type
            sourceObjectMetadata { id }
            targetObjectMetadata { id }
            sourceFieldMetadata { id name }
            targetFieldMetadata { id name }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_CREATE_OBJECT = """
mutation CreateObject($input: CreateOneObjectInput!) {
  createOneObject(input: $input) { id nameSingular description }
}
"""
_CREATE_FIELD = """
mutation CreateField($input: CreateOneFieldMetadataInput!) {
  createOneField(input: $input) { id name type isNullable }
}
"""

_PAGE_SIZE = 500


class SchemaConflict(RuntimeError):
    """Existing Twenty metadata prevents MALG's additive contract from applying."""


@dataclass(frozen=True)
class SchemaOperation:
    """One deterministic schema operation and whether it changes remote metadata."""

    action: str
    target: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable operation description without server IDs."""
        return {"action": self.action, "target": self.target, "payload": self.payload}


def load_manifest() -> dict[str, Any]:
    """Load the bundled contract manifest."""
    value = json.loads(MANIFEST_RESOURCE.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("CRM manifest must be an object")
    return value


def canonical_manifest(manifest: dict[str, Any] | None = None) -> str:
    """Return canonical managed metadata without server-generated values."""
    return json.dumps(manifest or load_manifest(), sort_keys=True, separators=(",", ":"))


def contract_hash(manifest: dict[str, Any] | None = None) -> str:
    """Return the stable SHA-256 contract hash."""
    return sha256(canonical_manifest(manifest).encode()).hexdigest()


def plan(manifest: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Describe the local contract operation order.

    The CLI combines this shape with a remote inspection; this helper remains useful
    for release tooling that only needs the deterministic manifest order.
    """
    desired = manifest or load_manifest()
    operations: list[dict[str, str]] = []
    for key, definition in desired["custom_objects"].items():
        operations.append(
            {"operation": "ensure_object", "name": definition["name_singular"], "key": key}
        )
        for field_name, field in definition["fields"].items():
            operations.append(
                {"operation": "ensure_field", "name": field_name, "type": field["type"]}
            )
    for relation in desired["relations"]:
        operations.append(
            {"operation": "ensure_relation", "name": relation["field"], "target": relation["to"]}
        )
    return operations


def inspect_payload() -> dict[str, Any]:
    """Return a secret-free local compatibility description."""
    manifest = load_manifest()
    return {
        "contract_version": manifest["contract_version"],
        "contract_hash": contract_hash(manifest),
        "standard_objects": manifest["standard_objects"],
        "custom_objects": manifest["custom_objects"],
        "relations": manifest["relations"],
    }


async def inspect_remote(client: TwentyClient) -> list[dict[str, Any]]:
    """Read every workspace object page for compatibility decisions.

    A repeated cursor is treated as a malformed upstream response rather than
    silently accepting a partial metadata view.
    """
    objects: list[dict[str, Any]] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    while True:
        data = await client.metadata_graphql(_OBJECTS_QUERY, {"after": after})
        connection = data.get("objects")
        if not isinstance(connection, Mapping):
            raise SchemaConflict("Twenty returned no object metadata.")
        edges = connection.get("edges")
        if not isinstance(edges, list):
            raise SchemaConflict("Twenty returned invalid object metadata.")
        for edge in edges:
            node = edge.get("node") if isinstance(edge, Mapping) else None
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("id"), str)
                or not isinstance(node.get("nameSingular"), str)
                or not node["id"]
                or not node["nameSingular"]
            ):
                raise SchemaConflict("Twenty returned malformed object metadata.")
            if node["id"] in seen_ids or node["nameSingular"] in seen_names:
                raise SchemaConflict("Twenty returned duplicate object metadata.")
            seen_ids.add(node["id"])
            seen_names.add(node["nameSingular"])
            objects.append(node)
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, Mapping) or not isinstance(page_info.get("hasNextPage"), bool):
            raise SchemaConflict("Twenty returned malformed object metadata pagination.")
        if not page_info["hasNextPage"]:
            return objects
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise SchemaConflict("Twenty returned an invalid metadata continuation cursor.")
        seen_cursors.add(cursor)
        after = cursor


async def reconcile(
    client: TwentyClient, manifest: dict[str, Any] | None = None, *, apply: bool
) -> list[dict[str, Any]]:
    """Check or add MALG-owned metadata and verify the resulting remote contract."""
    desired = manifest or load_manifest()
    remote_objects = await inspect_remote(client)
    operations = _build_operations(desired, remote_objects)
    if not apply:
        return [operation.as_dict() for operation in operations]

    objects_by_key = _objects_by_key(desired, remote_objects)
    for operation in operations:
        if operation.action == "create_object":
            result = await client.metadata_graphql(_CREATE_OBJECT, {"input": operation.payload})
            created = result.get("createOneObject")
            if not isinstance(created, Mapping) or not isinstance(created.get("id"), str):
                raise SchemaConflict("Twenty did not return the created MALG object ID.")
            objects_by_key[operation.target] = dict(created)
        elif operation.action == "create_field":
            object_metadata_id = _object_id(objects_by_key, operation.target)
            field = {**operation.payload, "objectMetadataId": object_metadata_id}
            await client.metadata_graphql(_CREATE_FIELD, {"input": {"field": field}})
        elif operation.action == "create_relation":
            source_key, target_key = operation.target.split(".", maxsplit=1)
            field = {
                **operation.payload,
                "objectMetadataId": _object_id(objects_by_key, source_key),
                "relationCreationPayload": {
                    **operation.payload["relationCreationPayload"],
                    "targetObjectMetadataId": _object_id(objects_by_key, target_key),
                },
            }
            field["relationCreationPayload"].pop("target", None)
            await client.metadata_graphql(_CREATE_FIELD, {"input": {"field": field}})

    remaining = _build_operations(desired, await inspect_remote(client))
    if remaining:
        raise SchemaConflict("Twenty metadata remained incomplete after apply.")
    return [operation.as_dict() for operation in operations]


async def check_contract(
    client: TwentyClient, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Read actual metadata and return the observed contract gate."""
    desired = manifest or load_manifest()
    pending = _build_operations(desired, await inspect_remote(client))
    return {
        "schema_compatible": not pending,
        "contract_version": desired["contract_version"],
        "contract_hash": contract_hash(desired),
        "pending_operations": [operation.as_dict() for operation in pending],
    }


def _field_map(object_name: str, object_: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return a complete metadata field projection or fail closed."""
    raw_fields = object_.get("fieldsList")
    if not isinstance(raw_fields, list):
        raise SchemaConflict(f"Twenty object {object_name!r} returned malformed fields metadata.")
    fields: dict[str, Mapping[str, Any]] = {}
    for field in raw_fields:
        if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
            raise SchemaConflict(
                f"Twenty object {object_name!r} returned malformed fields metadata."
            )
        if not field["name"] or field["name"] in fields:
            raise SchemaConflict(
                f"Twenty object {object_name!r} returned duplicate or unnamed fields."
            )
        fields[field["name"]] = field
    return fields


def _build_operations(
    desired: dict[str, Any], remote_objects: list[dict[str, Any]]
) -> list[SchemaOperation]:
    objects_by_name = {
        object_["nameSingular"]: object_
        for object_ in remote_objects
        if isinstance(object_.get("nameSingular"), str)
    }
    _check_standard_objects(desired, objects_by_name)
    operations: list[SchemaOperation] = []
    for key, definition in desired["standard_objects"].items():
        object_ = objects_by_name[definition["api_name"]]
        fields = _field_map(definition["api_name"], object_)
        for field_name, field_definition in definition.get("owned_fields", {}).items():
            existing_field = fields.get(field_name)
            if existing_field is None:
                operations.append(
                    SchemaOperation(
                        "create_field", key, _field_payload(field_name, field_definition)
                    )
                )
            else:
                _check_owned_field(key, field_name, existing_field, field_definition)
    managed_objects: dict[str, dict[str, Any]] = {}
    for key, definition in desired["custom_objects"].items():
        name = definition["name_singular"]
        existing = objects_by_name.get(name)
        if existing is None:
            operations.append(SchemaOperation("create_object", key, _object_payload(definition)))
            managed_objects[key] = {"fieldsList": []}
        else:
            _check_managed_object(name, existing)
            managed_objects[key] = existing
        fields = _field_map(name, managed_objects[key])
        for field_name, field_definition in definition["fields"].items():
            existing_field = fields.get(field_name)
            if existing_field is None:
                operations.append(
                    SchemaOperation(
                        "create_field", key, _field_payload(field_name, field_definition)
                    )
                )
            else:
                if field_name == "name" and existing_field.get("description") == "Name":
                    _check_field(name, field_name, existing_field, field_definition)
                else:
                    _check_owned_field(name, field_name, existing_field, field_definition)
    for relation in desired["relations"]:
        source = relation["from"]
        source_object = _source_object(source, desired, objects_by_name, managed_objects)
        fields = _field_map(source, source_object)
        existing_relation = fields.get(relation["field"])
        if existing_relation is None:
            operations.append(
                SchemaOperation(
                    "create_relation", f"{source}.{relation['to']}", _relation_payload(relation)
                )
            )
        elif existing_relation.get("type") != "RELATION":
            raise SchemaConflict(f"Twenty field {source}.{relation['field']} is not a relation.")
        else:
            _check_relation(
                source, relation, existing_relation, objects_by_name, desired, managed_objects
            )
    return operations


def _check_standard_objects(
    desired: dict[str, Any], objects_by_name: Mapping[str, dict[str, Any]]
) -> None:
    for key, definition in desired["standard_objects"].items():
        object_ = objects_by_name.get(definition["api_name"])
        if object_ is None:
            raise SchemaConflict(f"Twenty standard object {key!r} is unavailable.")
        fields = _field_map(definition["api_name"], object_)
        for field_name, expected_type in definition["required_fields"].items():
            field = fields.get(field_name)
            if field is None or field.get("type") != expected_type:
                raise SchemaConflict(f"Twenty standard field {key}.{field_name} is incompatible.")


def _check_relation(
    source: str,
    relation: Mapping[str, str],
    field: Mapping[str, Any],
    objects_by_name: Mapping[str, dict[str, Any]],
    desired: Mapping[str, Any],
    managed_objects: Mapping[str, dict[str, Any]],
) -> None:
    """Validate both installed relation endpoints and the inverse join column."""
    source_object = _source_object(source, dict(desired), objects_by_name, managed_objects)
    target = _source_object(relation["to"], dict(desired), objects_by_name, managed_objects)
    inverse = _field_map(relation["to"], target).get(relation["inverse"])
    topology = field.get("relation")
    reverse = inverse.get("relation") if inverse else None
    if inverse is None or not isinstance(topology, Mapping) or not isinstance(reverse, Mapping):
        raise SchemaConflict(f"Twenty relation {source}.{relation['field']} lacks topology.")
    for observed, from_object, to_object, from_field, to_field, direction in (
        (topology, source_object, target, field, inverse, "ONE_TO_MANY"),
        (reverse, target, source_object, inverse, field, "MANY_TO_ONE"),
    ):
        expected = {
            "sourceObjectMetadata": from_object.get("id"),
            "targetObjectMetadata": to_object.get("id"),
            "sourceFieldMetadata": from_field.get("id"),
            "targetFieldMetadata": to_field.get("id"),
        }
        if observed.get("type") != direction or any(
            not expected_id
            or not isinstance(observed.get(key), Mapping)
            or observed[key].get("id") != expected_id
            for key, expected_id in expected.items()
        ):
            raise SchemaConflict(
                f"Twenty relation {source}.{relation['field']} has incompatible topology."
            )
    if topology["targetFieldMetadata"].get("name") != relation["inverse"]:
        raise SchemaConflict(f"Twenty relation {source}.{relation['field']} has the wrong inverse.")
    settings = inverse.get("settings")
    if (
        not isinstance(settings, Mapping)
        or settings.get("joinColumnName") != f"{relation['inverse']}Id"
    ):
        raise SchemaConflict(
            f"Twenty relation {source}.{relation['field']} has the wrong join column."
        )
    for endpoint in (field, inverse):
        if (
            endpoint.get("description") != MANAGED_DESCRIPTION
            or endpoint.get("isNullable") is not True
        ):
            raise SchemaConflict(
                f"Twenty relation {source}.{relation['field']} is foreign or incompatible."
            )


def _check_managed_object(name: str, object_: Mapping[str, Any]) -> None:
    if object_.get("description") != MANAGED_DESCRIPTION:
        raise SchemaConflict(f"Twenty object {name!r} is not owned by MALG.")


def _check_field(
    object_name: str, field_name: str, field: Mapping[str, Any], definition: Mapping[str, Any]
) -> None:
    if (
        field.get("type") != definition["type"]
        or field.get("isNullable") is not definition["nullable"]
    ):
        raise SchemaConflict(f"Twenty field {object_name}.{field_name} is incompatible.")


def _check_owned_field(
    object_name: str, field_name: str, field: Mapping[str, Any], definition: Mapping[str, Any]
) -> None:
    _check_field(object_name, field_name, field, definition)
    if field.get("description") != MANAGED_DESCRIPTION:
        raise SchemaConflict(f"Twenty field {object_name}.{field_name} is not owned by MALG.")


def _objects_by_key(
    desired: dict[str, Any], remote_objects: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_name = {object_.get("nameSingular"): object_ for object_ in remote_objects}
    keyed: dict[str, dict[str, Any]] = {
        key: by_name[definition["name_singular"]]
        for key, definition in desired["custom_objects"].items()
        if definition["name_singular"] in by_name
    }
    keyed.update(
        {
            key: by_name[definition["api_name"]]
            for key, definition in desired["standard_objects"].items()
            if definition["api_name"] in by_name
        }
    )
    return keyed


def _object_id(objects_by_key: Mapping[str, Mapping[str, Any]], key: str) -> str:
    object_ = objects_by_key.get(key)
    object_id = object_.get("id") if object_ else None
    if not isinstance(object_id, str):
        raise SchemaConflict(f"Missing Twenty object ID for {key!r}.")
    return object_id


def _source_object(
    source: str,
    desired: dict[str, Any],
    objects_by_name: Mapping[str, dict[str, Any]],
    managed_objects: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    if source in managed_objects:
        return managed_objects[source]
    standard = desired["standard_objects"].get(source)
    if not isinstance(standard, Mapping):
        raise SchemaConflict(f"Unknown relation source {source!r}.")
    object_ = objects_by_name.get(standard["api_name"])
    if object_ is None:
        raise SchemaConflict(f"Twenty standard object {source!r} is unavailable.")
    return object_


def _object_payload(definition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "object": {
            "nameSingular": definition["name_singular"],
            "namePlural": definition["name_plural"],
            "labelSingular": _label(definition["name_singular"]),
            "labelPlural": _label(definition["name_plural"]),
            "description": MANAGED_DESCRIPTION,
            "skipNameField": True,
        }
    }


def _field_payload(name: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "label": _label(name),
        "type": definition["type"],
        "description": MANAGED_DESCRIPTION,
        "isNullable": definition["nullable"],
    }


def _relation_payload(relation: Mapping[str, str]) -> dict[str, Any]:
    return {
        "name": relation["field"],
        "label": _label(relation["field"]),
        "type": "RELATION",
        "description": MANAGED_DESCRIPTION,
        "isNullable": True,
        "relationCreationPayload": {
            "type": "ONE_TO_MANY",
            "target": relation["to"],
            "targetFieldLabel": _label(relation["inverse"]),
            "targetFieldIcon": "IconLink",
            "targetFieldName": relation["inverse"],
        },
    }


def _label(value: str) -> str:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    if spaced.startswith("malg "):
        return f"MALG {spaced.removeprefix('malg ').title()}"
    return spaced.title()
