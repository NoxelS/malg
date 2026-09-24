"""Shared deterministic external CRM metadata fixtures."""

from uuid import NAMESPACE_URL, uuid5

import pytest

from malg.crm.schema import MANAGED_DESCRIPTION, load_manifest


@pytest.fixture
def twenty_metadata():
    """Describe the installed relation shape without requiring a live workspace."""
    manifest = load_manifest()
    objects = {}
    for key, definition in manifest["standard_objects"].items():
        objects[key] = {
            "id": str(uuid5(NAMESPACE_URL, key)),
            "nameSingular": definition["api_name"],
            "description": "Native object",
            "fieldsList": [
                {
                    "id": str(uuid5(NAMESPACE_URL, key + name)),
                    "name": name,
                    "type": field_type,
                    "isNullable": True,
                }
                for name, field_type in definition["required_fields"].items()
            ],
        }
        for name, spec in definition.get("owned_fields", {}).items():
            objects[key]["fieldsList"].append(
                {
                    "id": str(uuid5(NAMESPACE_URL, key + name)),
                    "name": name,
                    "type": spec["type"],
                    "isNullable": spec["nullable"],
                    "description": MANAGED_DESCRIPTION,
                }
            )
    for key, definition in manifest["custom_objects"].items():
        objects[key] = {
            "id": str(uuid5(NAMESPACE_URL, key)),
            "nameSingular": definition["name_singular"],
            "description": MANAGED_DESCRIPTION,
            "fieldsList": [
                {
                    "id": str(uuid5(NAMESPACE_URL, key + name)),
                    "name": name,
                    "type": spec["type"],
                    "isNullable": spec["nullable"],
                    "description": MANAGED_DESCRIPTION,
                }
                for name, spec in definition["fields"].items()
            ],
        }
    for relation in manifest["relations"]:
        source, target = objects[relation["from"]], objects[relation["to"]]
        fields = []
        for obj, name in ((source, relation["field"]), (target, relation["inverse"])):
            field = {
                "id": str(uuid5(NAMESPACE_URL, obj["id"] + name)),
                "name": name,
                "type": "RELATION",
                "isNullable": True,
                "description": MANAGED_DESCRIPTION,
            }
            obj["fieldsList"].append(field)
            fields.append(field)
        for i, direction in enumerate(("ONE_TO_MANY", "MANY_TO_ONE")):
            start, end = (source, target) if i == 0 else (target, source)
            fields[i]["relation"] = {
                "type": direction,
                "sourceObjectMetadata": {"id": start["id"]},
                "targetObjectMetadata": {"id": end["id"]},
                "sourceFieldMetadata": {"id": fields[i]["id"], "name": fields[i]["name"]},
                "targetFieldMetadata": {"id": fields[1 - i]["id"], "name": fields[1 - i]["name"]},
            }
            fields[i]["settings"] = {"relationType": direction}
        fields[1]["settings"]["joinColumnName"] = relation["inverse"] + "Id"
    return {
        "data": {
            "objects": {
                "edges": [{"node": obj} for obj in objects.values()],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }
