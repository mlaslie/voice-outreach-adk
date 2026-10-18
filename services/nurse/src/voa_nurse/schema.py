"""A2UI v0.9 schema check, using the JSON schemas shipped in ``a2ui-agent-sdk``.

Every outgoing card is validated before it is sent: a card that would render
blank in Gemini Enterprise is sent as text instead, and the problem is logged.
"""

from __future__ import annotations

import json
import pathlib
from functools import cache

import a2ui
import jsonschema
from referencing import Registry, Resource

_ASSETS = pathlib.Path(next(iter(a2ui.__path__))) / "assets" / "0.9"


@cache
def validator() -> jsonschema.Draft202012Validator:
    def load(name: str) -> dict:
        return json.loads((_ASSETS / name).read_text())

    s2c, catalog, common = (
        load("server_to_client.json"),
        load("catalog.json"),
        load("common_types.json"),
    )
    registry = Registry().with_resources(
        [
            (s2c["$id"], Resource.from_contents(s2c)),
            (catalog["$id"], Resource.from_contents(catalog)),
            # server_to_client.json refers to the catalog by this alias.
            (
                "https://a2ui.org/specification/v0_9/catalog.json",
                Resource.from_contents(catalog),
            ),
            (common["$id"], Resource.from_contents(common)),
        ]
    )
    return jsonschema.Draft202012Validator(s2c, registry=registry)


def problems(messages: list[dict]) -> list[str]:
    """Schema errors plus the structural rules the schema cannot express:
    unique ids, a ``root`` component and no dangling references."""
    out = [e.message for m in messages for e in validator().iter_errors(m)]
    comps = next(
        (m["updateComponents"]["components"] for m in messages if "updateComponents" in m), []
    )
    ids = [c.get("id") for c in comps]
    if len(ids) != len(set(ids)):
        out.append("duplicate component ids")
    if "root" not in ids:
        out.append("no root component")
    known = set(ids)
    for c in comps:
        refs = list(c.get("children", [])) + [
            c[k] for k in ("child", "trigger", "content") if k in c
        ]
        refs += [t.get("child") for t in c.get("tabs", [])]
        out += [f"dangling reference {r!r} in {c.get('id')}" for r in refs if r not in known]
    return out
