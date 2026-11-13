import configparser
import subprocess
import tempfile
import zipfile
from pathlib import Path


workflow = Path(".github/workflows/release.yml").read_text()
assert '      - "v*"' in workflow
assert workflow.index("run: uv python install 3.13") < workflow.index("run: uv build")
assert workflow.index("run: uv build") < workflow.index("run: uv publish")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    subprocess.run(["uv", "build", "--out-dir", tmp], check=True)
    assert len(list(root.glob("*.tar.gz"))) == 1
    wheel, = root.glob("*.whl")
    with zipfile.ZipFile(wheel) as package:
        metadata, = (name for name in package.namelist() if name.endswith("/entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(package.read(metadata).decode())
        assert dict(parser["console_scripts"]) == {
            "bitchat": "bitchat_cli.cli:main",
            "bchat": "bitchat_cli.cli:main",
        }
        assert "bitchat_cli/cli.py" in package.namelist()
print("Tagged build order and both wheel aliases passed")
