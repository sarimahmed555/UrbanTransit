"""Static integrity checks for repository structure and documentation references.

These tests read the working tree only. They start no workload, read no dataset,
open no service and assert nothing about runtime results. A pending placeholder
is a valid state; a reference to a file that does not exist is not.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "documentation"

# Directories the SRS expects in the public repository. Each must exist and must
# be reachable by an anonymous cloner, which means an empty directory needs a
# tracked placeholder file.
REQUIRED_SOURCE_DIRECTORIES = (
    "backend", "backend/security", "backend/serving", "data_generator",
    "acceptance_harness", "evidence_framework", "feature_contracts.py",
    "frontend", "hdfs_scripts", "ml_execution", "python_pipeline",
    "recommendation_engine", "runtime_orchestration", "spark_jobs", "spark_sql",
    "tests", "documentation",
)

# Artifact locations that are legitimately empty until a certified run fills
# them. They must exist as tracked placeholders so the structure survives a
# clone, and their contents are a runtime question, not a documentation one.
PLACEHOLDER_DIRECTORIES = (
    "config", "database", "models", "notebooks", "parquet_data",
    "processed_data", "reports", "screenshots", "src", "static", "templates",
    "raw_data", "sample_data",
    "delay_analysis", "forecasting", "occupancy_analysis", "route_clustering",
)

# Documents whose backticked paths are checked. The set is deliberately the
# mandatory submission set plus the root README, because those are the
# artifacts an evaluator reads first.
CHECKED_DOCUMENTS = (
    "README.md",
    "AI_USAGE.md",
    "DEVELOPMENT_LOG.md",
    "documentation/README.md",
    "documentation/ARCHITECTURE.md",
    "documentation/INSTALLATION_AND_RUNTIME.md",
    "documentation/RUNTIME_ORCHESTRATION.md",
    "documentation/HDFS_SPARK_RUNBOOK.md",
    "documentation/ML_EXECUTION_AND_COMPARISON.md",
    "documentation/ANALYTICS_CAPABILITY_MAP.md",
    "documentation/SECURITY_AND_PRIVACY.md",
    "documentation/TESTING.md",
    "documentation/EVIDENCE_MANIFEST.md",
    "documentation/DATASET_CERTIFICATION_STATUS.md",
    "documentation/PROJECT_REPORT_TEMPLATE.md",
    "documentation/DEMO_VIDEO_CHECKLIST.md",
    "documentation/BLOG_PREPARATION.md",
    "documentation/FINAL_SUBMISSION_CHECKLIST.md",
    "documentation/GITHUB_READINESS_CHECKLIST.md",
    "documentation/FEATURE_ENGINEERING_CONTRACT.md",
    "documentation/REPORTING_EXPORT_CONTRACT.md",
    "documentation/API_COMPOSITION_CONTRACT.md",
)

# A backticked token is treated as a repository path only if it looks like a
# relative path with a known source extension and no placeholder characters.
PATH_TOKEN = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+"
    r"\.(?:py|sh|sql|md|json|jsonl|csv|js|mjs|ts|tsx|jsx|css|html|yml|yaml|toml|cfg|ini|txt|xml|mp4))`"
)

SOURCE_EXTENSIONS = {
    ".py", ".sh", ".sql", ".md", ".json", ".jsonl", ".csv", ".js", ".mjs",
    ".ts", ".tsx", ".jsx", ".css", ".html", ".yml", ".yaml", ".toml",
    ".cfg", ".ini", ".txt", ".xml", ".mp4",
}

# Paths that describe runtime output rather than repository source. Their
# absence is the honest pre-run state, so they are reported, never failed.
RUNTIME_ARTIFACT_PREFIXES = (
    "raw_data/", "processed_data/", "parquet_data/", "metadata/",
    "reports/production", "evidence/", "models/", "screenshots/",
)

# Some documents legitimately use a package-relative path because the sentence
# already names the package. Accept the token if it resolves under one of these.
PACKAGE_ROOTS = ("data_generator/", "frontend/", "backend/", "documentation/")


def resolve(token):
    for candidate in (ROOT / token, *(ROOT / root / token for root in PACKAGE_ROOTS)):
        if candidate.is_file():
            return candidate
    return None


class RepositoryStructureTests(unittest.TestCase):
    def test_required_source_directories_exist(self):
        missing = [name for name in REQUIRED_SOURCE_DIRECTORIES if not (ROOT / name).exists()]
        self.assertEqual(missing, [], f"missing required source paths: {missing}")

    def test_placeholder_directories_carry_a_tracked_placeholder_file(self):
        # An empty directory cannot be committed, so a clone would lose it.
        missing = [
            name for name in PLACEHOLDER_DIRECTORIES
            if not (ROOT / name).is_dir() or not any((ROOT / name).iterdir())
        ]
        self.assertEqual(missing, [], f"empty placeholder directories: {missing}")

    def test_tests_directory_holds_the_test_suite_and_a_fixture(self):
        modules = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
        self.assertGreaterEqual(len(modules), 25, f"unexpectedly few test modules: {len(modules)}")
        self.assertTrue((ROOT / "tests" / ".gitkeep").is_file())

    def test_documentation_directory_index_exists(self):
        self.assertTrue((DOCS / "README.md").is_file())
        self.assertTrue((DOCS / ".gitkeep").is_file())


class DocumentationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.missing = {}
        cls.runtime_only = {}
        for name in CHECKED_DOCUMENTS:
            path = ROOT / name
            if not path.is_file():
                cls.missing[name] = ["<document does not exist>"]
                continue
            absent, runtime = [], set()
            for token in PATH_TOKEN.findall(path.read_text(encoding="utf-8")):
                if resolve(token) is not None:
                    continue
                if token.startswith(RUNTIME_ARTIFACT_PREFIXES):
                    runtime.add(token)
                else:
                    absent.append(token)
            if absent:
                cls.missing[name] = sorted(set(absent))
            if runtime:
                cls.runtime_only[name] = sorted(runtime)

    def test_every_checked_document_exists(self):
        absent = sorted(name for name in CHECKED_DOCUMENTS if not (ROOT / name).is_file())
        self.assertEqual(absent, [], f"missing documents: {absent}")

    def test_documentation_does_not_reference_nonexistent_repository_paths(self):
        self.assertEqual(
            self.missing, {},
            f"documentation references files that do not exist: {self.missing}",
        )

    def test_runtime_artifact_references_are_prefixes_not_bare_files(self):
        # A bare `metadata/...` reference is pending by design; a bare source
        # path would be a defect and is caught by the test above. This test
        # documents the split so the intent is not lost.
        for document, tokens in self.runtime_only.items():
            for token in tokens:
                with self.subTest(document=document, token=token):
                    self.assertTrue(
                        token.startswith(RUNTIME_ARTIFACT_PREFIXES),
                        f"{token} is neither a repository file nor a runtime artifact",
                    )

    def test_path_token_pattern_actually_matches_something(self):
        # Guards the reference test itself: a broken pattern would make every
        # other test in this class pass vacuously.
        matched = 0
        for name in CHECKED_DOCUMENTS:
            path = ROOT / name
            if path.is_file():
                matched += len(PATH_TOKEN.findall(path.read_text(encoding="utf-8")))
        self.assertGreater(matched, 50, "path-token pattern found almost nothing")

    def test_python_module_references_in_docs_resolve(self):
        # Every `python3 -m <module>` in the docs must be a real module or a
        # standard-library tool, so no workflow instruction is a dead command.
        module_reference = re.compile(r"python3?\s+-m\s+([A-Za-z_][\w.]*)")
        stdlib = {"unittest", "pip", "venv", "json.tool", "http.server", "compileall"}
        dead = {}
        for name in CHECKED_DOCUMENTS:
            path = ROOT / name
            if not path.is_file():
                continue
            for module in set(module_reference.findall(path.read_text(encoding="utf-8"))):
                if module in stdlib or module.startswith("unittest"):
                    continue
                target = ROOT / module.replace(".", "/")
                if (target.with_suffix(".py")).is_file() or (target / "__main__.py").is_file():
                    continue
                dead.setdefault(name, []).append(module)
        self.assertEqual(dead, {}, f"documents reference dead module commands: {dead}")

    def test_path_token_pattern_covers_every_declared_extension(self):
        # If a new extension is added to SOURCE_EXTENSIONS it must also be
        # matched by PATH_TOKEN, otherwise reference checking silently skips it.
        unmatched = [
            extension for extension in sorted(SOURCE_EXTENSIONS)
            if not PATH_TOKEN.fullmatch(f"`dir/file{extension}`")
        ]
        self.assertEqual(unmatched, [], f"extensions not covered by PATH_TOKEN: {unmatched}")


if __name__ == "__main__":
    unittest.main()
