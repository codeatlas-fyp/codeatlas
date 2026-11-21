"""How labellers name code, and how that maps to CodeAtlas entity ids (CA-02).

Labellers write an entity the way they see it in the repository, `path.py::Qualified.name`
(for example `libcloud/compute/drivers/gce.py::GCENodeDriver.ex_create_instancegroup`).
CodeAtlas ids are dotted module paths
(`libcloud.compute.drivers.gce.GCENodeDriver.ex_create_instancegroup`). Both directions
are one deterministic rule, the same one `code.extract_entities` uses, so a label never
depends on CodeAtlas output.
"""

from __future__ import annotations

from codeatlas.analyze.code import _module_name_from_path
from codeatlas.schema import CodeEntity

REF_SEPARATOR = "::"


def entity_id_from_ref(ref: str) -> str:
    """`pkg/mod.py::Cls.meth` -> `pkg.mod.Cls.meth`; `pkg/mod.py` -> `pkg.mod`; ids unchanged."""
    ref = ref.strip()
    if REF_SEPARATOR not in ref:  # a whole module may be written as its file path
        return _module_name_from_path(ref.replace("\\", "/"), "") if ref.endswith(".py") else ref
    path, _, qualname = ref.partition(REF_SEPARATOR)
    module = _module_name_from_path(path.strip().replace("\\", "/"), "")
    qualname = qualname.strip()
    return f"{module}.{qualname}" if qualname else module


def entity_ref(entity: CodeEntity) -> str:
    """The labeller's form of an entity: `file::qualified name inside the module`."""
    module = _module_name_from_path(entity.file, "")
    qualname = entity.entity_id.removeprefix(module).lstrip(".")
    return f"{entity.file}{REF_SEPARATOR}{qualname}" if qualname else entity.file


__all__ = ["REF_SEPARATOR", "entity_id_from_ref", "entity_ref"]
