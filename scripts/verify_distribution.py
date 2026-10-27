"""Inspect distributions, rebuild the sdist and smoke-test the wheel."""

from __future__ import annotations

import email
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], directory: Path) -> None:
    subprocess.run(arguments, cwd=directory, check=True)


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    (wheel,) = ROOT.joinpath("dist").glob("*.whl")
    (sdist,) = ROOT.joinpath("dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith("/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_name))
        assert metadata["Name"] == "golded-ftn-sqlite"
        assert metadata["Version"] == project["version"]
        assert metadata["License-Expression"] == "MIT"
        assert any(name.endswith("/py.typed") for name in names)
        assert any(name.endswith("/licenses/LICENSE") for name in names)

    with tempfile.TemporaryDirectory(prefix="golded-sqlite-distribution-") as temporary:
        work = Path(temporary)
        with tarfile.open(sdist) as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                assert not Path(member.name).is_absolute()
                assert ".." not in parts
                assert not {".env", ".git", ".venv", "__pycache__"}.intersection(parts)
            archive.extractall(work, filter="data")
        (source,) = work.iterdir()
        run(["uv", "build", "--no-sources", "--wheel"], source)
        (rebuilt,) = source.joinpath("dist").glob("*.whl")
        environment = work / "consumer"
        run(["uv", "venv", "--python", sys.executable, str(environment)], work)
        python = str(environment / "bin" / "python")
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                python,
                "--no-cache",
                "--reinstall",
                str(rebuilt),
            ],
            work,
        )
        run(["uv", "pip", "check", "--python", python], work)
        smoke = """
from pathlib import Path
from tempfile import TemporaryDirectory
from golded_ftn import OutgoingMessage
from golded_ftn_sqlite import (
    AreaDefinition,
    AreaKind,
    SqliteDatabase,
    SqliteReader,
    SqliteWriter,
)

with TemporaryDirectory() as directory:
    path = Path(directory) / "smoke.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(code="FIDO.SQLITE", name="SQLite", kind=AreaKind.ECHOMAIL),
    )
    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Installed wheel",
                body_text="This did not import from the checkout.",
            )
        )
    (message,) = SqliteReader().read(path)
    assert message.subject == "Installed wheel"
"""
        run([python, "-I", "-c", smoke], work)

    print("Wheel metadata, sdist rebuild and clean consumer behavior passed.")


if __name__ == "__main__":
    main()
