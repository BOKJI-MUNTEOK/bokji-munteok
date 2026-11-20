"""Streamlit Community Cloud용 hwplib 자동 준비: 켤 때만, 검증된 jar만 쓴다. 네트워크는 가짜로 대신한다."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fill_hwp


class AutoSetup(unittest.TestCase):
    def setUp(self):
        fill_hwp._setup_tried = False

    def test_off_by_default_makes_no_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HWPLIB_DIR": tmp}), \
                patch.object(fill_hwp, "ROOT_DIR", Path(tmp)), \
                patch("urllib.request.urlopen", side_effect=AssertionError("must not download")):
            os.environ.pop("HWPLIB_AUTO_SETUP", None)
            fill_hwp._resolve_hwplib_runtime()

    def test_rejects_jar_with_wrong_checksum(self):
        with tempfile.TemporaryDirectory() as tmp, patch("shutil.which", return_value="/usr/bin/javac"), \
                patch("urllib.request.urlopen", return_value=io.BytesIO(b"not the real jar")), \
                patch("subprocess.run", side_effect=AssertionError("must not compile")):
            self.assertFalse(fill_hwp.auto_setup(Path(tmp)))
            self.assertFalse((Path(tmp) / fill_hwp.JAR_NAME).exists())

    def test_without_javac_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, patch("shutil.which", return_value=None), \
                patch("urllib.request.urlopen", side_effect=AssertionError("must not download")):
            self.assertFalse(fill_hwp.auto_setup(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
