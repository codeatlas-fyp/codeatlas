"""The labeller's path.py::Name form and CodeAtlas entity ids map both ways (CA-02)."""

from pathlib import Path

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.semantic_gate import read_dev_labels
from codeatlas.analyze.semantic_labels import entity_id_from_ref, entity_ref

SOURCE = b"class Fees:\n    def late(self):\n        return 1\n\n\ndef total():\n    return 2\n"


def test_ref_round_trips_for_every_entity() -> None:
    for entity in extract_entities(file_path="lib/billing/fees.py", source=SOURCE):
        assert entity_id_from_ref(entity_ref(entity)) == entity.entity_id


def test_known_forms() -> None:
    assert entity_id_from_ref("lib/billing/fees.py::Fees.late") == "lib.billing.fees.Fees.late"
    assert entity_id_from_ref("pkg/__init__.py::setup") == "pkg.setup"
    assert entity_id_from_ref(r"lib\billing\fees.py::total") == "lib.billing.fees.total"
    assert entity_id_from_ref("  lib.billing.fees.total ") == "lib.billing.fees.total"


def test_labels_csv_accepts_either_form(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "criterion_id,criterion_text,entity_id\n"
        "K-1:v1:ac1,Charge late fees.,lib/billing/fees.py::Fees.late\n"
        "K-1:v1:ac1,Charge late fees.,lib.billing.fees.total\n",
        encoding="utf-8",
    )
    (crit,) = read_dev_labels(labels)
    assert crit.relevant == {"lib.billing.fees.Fees.late", "lib.billing.fees.total"}
