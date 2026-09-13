import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from writing_memory.core import Store
from writing_memory.prompt_source import fetch_prompt


class PromptSourceTests(unittest.TestCase):
    def test_version_is_preserved_and_no_secret_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "assets"
            Store(root).init()
            content = {"name": "team-extraction", "version": 7, "type": "text", "prompt": "提炼有限范围的候选经验。", "labels": ["production"]}
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps(content).encode())
            env = {"LANGFUSE_HOST": "https://example.invalid", "LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "do-not-save"}
            with patch.dict(os.environ, env), patch("writing_memory.prompt_source.build_opener", return_value=opener):
                result = fetch_prompt(root, "team-extraction", version=7)
            self.assertEqual(result["version"], 7)
            saved = next((root / "prompt_snapshots").glob("*.json")).read_text()
            self.assertNotIn("do-not-save", saved)
            self.assertEqual(json.loads(saved)["provider"], "langfuse")

    def test_remote_http_is_rejected_before_network(self):
        with patch.dict(os.environ, {"LANGFUSE_HOST": "http://example.invalid", "LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s"}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                fetch_prompt(Path("unused"), "name")

    def test_wrong_version_never_becomes_an_extraction_snapshot(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"name":"name","version":2,"type":"text","prompt":"text"}')
        with patch.dict(os.environ, {"LANGFUSE_HOST": "https://example.invalid", "LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s"}), patch("writing_memory.prompt_source.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "版本与请求不一致"):
                fetch_prompt(Path("unused"), "name", version=1)
