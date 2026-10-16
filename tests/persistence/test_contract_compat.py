"""P4 contract change is additive only; published schemas match the models."""
import json
import unittest
from pathlib import Path
from typing import get_args

from app.models.contracts import StopCode
from scripts import export_schemas

P3_STOP_CODES = {"REVIEW_REQUIRED", "MISSING_DATA", "UNKNOWN_ROUTE", "AGENT_ERROR", "INVALID_RESULT", "TIMEOUT",
                 "CANCELLED", "STEP_LIMIT", "INVALID_PLAN", "INTERNAL_ERROR"}
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "medrixa.prototype.v1"


class ContractCompatibilityTests(unittest.TestCase):
    def test_stop_codes_only_extended(self):
        self.assertEqual(set(get_args(StopCode)), P3_STOP_CODES | {"INTERRUPTED"})

    def test_published_schemas_match_models(self):
        source = Path(export_schemas.__file__).read_text(encoding="utf-8")
        for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
            name = path.name.removesuffix(".schema.json")
            with self.subTest(schema=name):
                self.assertIn(name, source)
                model = vars(export_schemas)[name]
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), model.model_json_schema())


if __name__ == "__main__":
    unittest.main()
