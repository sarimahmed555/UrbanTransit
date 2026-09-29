"""Static checks for mandatory installation and AI-declaration documentation.

These tests assert that required guidance exists. They never run a workload,
read a dataset, or assert that any installation or production run happened.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLATION = ROOT / "documentation" / "INSTALLATION_AND_RUNTIME.md"
AI_USAGE = ROOT / "AI_USAGE.md"

# SRS DEL-10 mandatory installation items -> a distinctive token per section.
REQUIRED_INSTALLATION_SECTIONS = {
    "Python installation": "python3 --version",
    "Java installation": "java -version",
    "Hadoop installation": "hadoop version",
    "HDFS configuration": "fs.defaultFS",
    "Apache Spark installation": "spark-submit --version",
    "PySpark configuration": "import pyspark",
    "Virtual environment setup": "python3 -m venv",
    "Database setup": "python3 -m backend.serving migrate",
    "Dataset generation": "python3 -m data_generator.preflight",
    "Spark execution": "python3 -m spark_jobs.pipeline",
    "Model execution": "python3 -m ml_execution",
    "Web application execution": "backend.fastapi_app",
    "Test execution": "python3 -m unittest discover -s tests -v",
    "Troubleshooting": "| Symptom | Cause | Action |",
}

# SRS DEL-16-01..07 mandatory declaration fields.
REQUIRED_DECLARATION_FIELDS = (
    "**Tool name:**",
    "**Purpose:**",
    "**Type of help:**",
    "**Files affected:**",
    "**Modifications made:**",
    "**Testing completed:**",
    "**Verifying team member:**",
)

# Implemented components that must stay discoverable from the guide.
REQUIRED_RUNTIME_ENTRYPOINTS = (
    "runtime_orchestration preflight",
    "hdfs_scripts/prepare_urbantransit_dirs.sh",
    "hdfs_scripts/ingest_certified_dataset.sh",
    "python3 -m runtime_orchestration.publish",
    "python_pipeline",
    "runtime_orchestration compare",
    "recommendation_engine what-if",
)


class InstallationDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INSTALLATION.read_text(encoding="utf-8")
        # Prose wraps across lines; normalise it before substring assertions.
        cls.flat = " ".join(cls.text.split())

    def test_guide_exists_and_is_not_a_placeholder(self):
        self.assertTrue(INSTALLATION.is_file())
        self.assertGreater(len(self.text.splitlines()), 100)

    def test_every_mandatory_installation_item_is_documented(self):
        missing = {
            item: token
            for item, token in REQUIRED_INSTALLATION_SECTIONS.items()
            if token not in self.flat
        }
        self.assertEqual(missing, {}, f"undocumented installation items: {sorted(missing)}")

    def test_every_required_item_has_its_own_heading(self):
        headings = {
            re.sub(r"^\d+\.\s*", "", title)
            for title in re.findall(r"^##+\s+(.+?)\s*$", self.text, re.MULTILINE)
        }
        missing = sorted(set(REQUIRED_INSTALLATION_SECTIONS) - headings)
        self.assertEqual(missing, [], f"missing installation sections: {missing}")

    def test_implemented_runtime_entrypoints_are_referenced(self):
        missing = [name for name in REQUIRED_RUNTIME_ENTRYPOINTS if name not in self.flat]
        self.assertEqual(missing, [], f"undocumented entry points: {missing}")
        self.assertNotIn("python3 -m runtime_orchestration publish", self.flat)

    def test_referenced_repository_paths_exist(self):
        referenced = set(re.findall(r"`((?:[a-z_]+/)+[A-Za-z0-9_.]+\.(?:py|sh|sql))`", self.text))
        missing = sorted(name for name in referenced if not (ROOT / name).is_file())
        self.assertEqual(missing, [], f"guide references missing files: {missing}")

    def test_guide_does_not_claim_a_completed_production_run(self):
        lowered = self.text.lower()
        for claim in (
            "run successfully",
            "successfully installed",
            "production run completed",
            "metrics were produced",
        ):
            self.assertNotIn(claim, lowered)


class AiUsageDeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = AI_USAGE.read_text(encoding="utf-8")
        cls.flat = " ".join(cls.text.split())

    def test_declaration_file_exists(self):
        self.assertTrue(AI_USAGE.is_file())

    def test_every_mandatory_declaration_field_is_present(self):
        missing = [field for field in REQUIRED_DECLARATION_FIELDS if field not in self.text]
        self.assertEqual(missing, [], f"missing AI declaration fields: {missing}")

    def test_post_certification_implementation_is_declared(self):
        for area in (
            "runtime_orchestration/",
            "hdfs_scripts/",
            "spark_jobs/",
            "python_pipeline/",
            "ml_execution/",
            "recommendation_engine/",
            "evidence_framework/",
            "backend/",
        ):
            self.assertIn(area, self.text, f"undeclared implementation area: {area}")

    def test_declaration_does_not_overclaim_executed_verification(self):
        lowered = self.flat.lower()
        self.assertIn(
            "no hdfs, spark, ml, or postgresql production job was executed", lowered
        )
        self.assertIn("a skip is not a pass", lowered)


if __name__ == "__main__":
    unittest.main()
