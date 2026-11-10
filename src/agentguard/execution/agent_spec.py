"""Launch data and explicit Python source selection; never imports agent modules."""
from pathlib import Path
import hashlib
from pydantic import BaseModel, ConfigDict, Field


class AgentLaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
    source_dir: Path
    include_files: list[str] = Field(default_factory=list)

    def sources(self) -> tuple[dict[str, bytes], str]:
        root = self.source_dir.resolve(strict=True)
        module = self.reference.split(":")[0]
        target = module.replace(".", "/") + ".py"
        names = {target, *self.include_files}
        parts = target.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            init = "/".join(parts[:depth]) + "/__init__.py"
            if (root / init).exists():
                names.add(init)
        if len(names) > 128:
            raise ValueError("Too many source files.")
        files = {}
        for name in sorted(names):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py" or "\\" in name or ":" in name:
                raise ValueError("Only explicit relative Python source files are supported.")
            path = root / relative
            for parent in [path, *path.parents]:
                if parent == root:
                    break
                if parent.is_symlink():
                    raise ValueError("Symlink source files/directories are not allowed.")
            if not path.resolve(strict=True).is_relative_to(root) or not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("Source escapes its source directory or is a hard link.")
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Source file exceeds limit.")
            with path.open("rb") as source:
                content = source.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Source file exceeds limit.")
            files[relative.as_posix()] = content
        if sum(map(len, files.values())) > 8 * 1024 * 1024:
            raise ValueError("Source bundle exceeds limit.")
        digest = hashlib.sha256()
        for name, content in files.items():
            digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
        return files, digest.hexdigest()
