"""Static structure checks for the submission documentation set.

These tests read documentation files only. They never run a workload, read a
dataset, connect to a service, or assert that any runtime evidence exists.
A pending placeholder is a valid state; a fabricated claim is not.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "documentation"

PENDING = "PENDING CERTIFIED RUNTIME EVIDENCE"

# Every mandatory submission-documentation artifact prepared by this pass.
REQUIRED_DOCUMENTS = (
    "README.md",
    "ARCHITECTURE.md",
    "INSTALLATION_AND_RUNTIME.md",
    "RUNTIME_ORCHESTRATION.md",
    "HDFS_SPARK_RUNBOOK.md",
    "ML_EXECUTION_AND_COMPARISON.md",
    "ANALYTICS_CAPABILITY_MAP.md",
    "SECURITY_AND_PRIVACY.md",
    "TESTING.md",
    "EVIDENCE_MANIFEST.md",
    "DATASET_CERTIFICATION_STATUS.md",
    "PROJECT_REPORT_TEMPLATE.md",
    "DEMO_VIDEO_CHECKLIST.md",
    "BLOG_PREPARATION.md",
    "FINAL_SUBMISSION_CHECKLIST.md",
    "GITHUB_READINESS_CHECKLIST.md",
)

# DEL-14 mandatory demonstration workflows, in order.
VIDEO_WORKFLOWS = (
    "Login", "Dataset generation", "HDFS", "Spark processing",
    "Data-quality analysis", "Passenger-flow analysis",
    "Origin-destination analysis", "Peak-period detection",
    "Overcrowding detection", "Underutilization analysis",
    "Route-performance analysis", "Delay analysis", "Delay prediction",
    "Route clustering", "Demand forecasting", "Occupancy forecasting",
    "Spark model", "Python model", "Dual-pipeline comparison",
    "Recommendation engine", "What-if analysis", "Network map", "Dashboard",
    "Report generation",
)

# DEL-15 mandatory blog topics.
BLOG_TOPICS = (
    "Transport problem", "Big Data architecture", "Dataset creation",
    "Data quality", "Hadoop", "HDFS", "Apache Spark", "PySpark", "Spark SQL",
    "Feature engineering", "Passenger flow", "Origin-destination analysis",
    "Route performance", "Delay analytics", "Forecasting", "Occupancy",
    "Route clustering", "Spark MLlib", "Python Data Science",
    "Dual-pipeline comparison", "Challenges", "Testing", "Performance",
    "Security", "Lessons learned", "Limitations", "Future enhancements",
)

# DEL-17 final submission artifacts.
FINAL_ARTIFACTS = tuple(f"DEL-17-{index:02d}" for index in range(1, 22))

# Phrases that would assert a completed run, install or measurement.
FORBIDDEN_CLAIMS = (
    "run successfully",
    "successfully installed",
    "production run completed",
    "metrics were produced",
    "all tests passed",
    "we achieved 8",
)


class SubmissionDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = {
            name: (DOCS / name).read_text(encoding="utf-8")
            for name in REQUIRED_DOCUMENTS
            if (DOCS / name).is_file()
        }
        cls.flat = {
            name: " ".join(text.split()) for name, text in cls.texts.items()
        }
        cls.index = cls.texts.get("README.md", "")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_every_required_document_exists(self):
        missing = sorted(
            name for name in REQUIRED_DOCUMENTS if not (DOCS / name).is_file()
        )
        self.assertEqual(missing, [], f"missing submission documents: {missing}")

    def test_documentation_index_only_links_existing_files(self):
        targets = set(re.findall(r"\]\((?!https?:)([^)#]+)\)", self.index))
        missing = sorted(
            target
            for target in targets
            if not (DOCS / target).resolve().is_file()
            and not (ROOT / target).resolve().is_file()
        )
        self.assertEqual(missing, [], f"index links missing targets: {missing}")

    def test_mandatory_checklist_items_are_enumerated(self):
        video = self.flat.get("DEMO_VIDEO_CHECKLIST.md", "")
        missing_video = [item for item in VIDEO_WORKFLOWS if item not in video]
        self.assertEqual(missing_video, [], f"video items missing: {missing_video}")

        blog = self.flat.get("BLOG_PREPARATION.md", "")
        missing_blog = [topic for topic in BLOG_TOPICS if topic not in blog]
        self.assertEqual(missing_blog, [], f"blog topics missing: {missing_blog}")

        final = self.flat.get("FINAL_SUBMISSION_CHECKLIST.md", "")
        missing_final = [item for item in FINAL_ARTIFACTS if item not in final]
        self.assertEqual(missing_final, [], f"final artifacts missing: {missing_final}")

    def test_runtime_dependent_documents_declare_pending_evidence(self):
        runtime_bound = (
            "ARCHITECTURE.md",
            "ML_EXECUTION_AND_COMPARISON.md",
            "ANALYTICS_CAPABILITY_MAP.md",
            "TESTING.md",
            "EVIDENCE_MANIFEST.md",
            "DATASET_CERTIFICATION_STATUS.md",
            "PROJECT_REPORT_TEMPLATE.md",
            "DEMO_VIDEO_CHECKLIST.md",
            "BLOG_PREPARATION.md",
            "FINAL_SUBMISSION_CHECKLIST.md",
            "GITHUB_READINESS_CHECKLIST.md",
        )
        missing = sorted(
            name for name in runtime_bound if PENDING not in self.flat.get(name, "")
        )
        self.assertEqual(
            missing, [], f"documents without a pending-evidence marker: {missing}"
        )

    def test_no_document_asserts_a_completed_runtime(self):
        offenders = []
        for name, text in self.flat.items():
            lowered = text.lower()
            for claim in FORBIDDEN_CLAIMS:
                if claim in lowered:
                    offenders.append(f"{name}: {claim}")
        self.assertEqual(offenders, [], f"unsupported completion claims: {offenders}")

    def test_readme_declares_status_and_workflow_references(self):
        lowered = self.readme.lower()
        self.assertIn("pending certified runtime evidence", lowered)
        self.assertIn("a skip is not a pass", lowered)
        for reference in (
            "documentation/README.md",
            "documentation/INSTALLATION_AND_RUNTIME.md",
            "documentation/DEMO_VIDEO_CHECKLIST.md",
            "documentation/BLOG_PREPARATION.md",
            "documentation/FINAL_SUBMISSION_CHECKLIST.md",
            "documentation/GITHUB_READINESS_CHECKLIST.md",
            "AI_USAGE.md",
        ):
            self.assertIn(reference, self.readme, f"README missing reference: {reference}")


if __name__ == "__main__":
    unittest.main()
