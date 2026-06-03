from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
RESOURCE_ROOTS = {"references", "scripts", "assets"}


@dataclass(frozen=True)
class SkillSpec:
    name: str
    what_it_does: str
    when_to_use: str
    description: str
    path: Path
    body: str
    resources: list[str]
    always_active: bool = False

    def to_catalog_item(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "what_it_does": self.what_it_does,
            "when_to_use": self.when_to_use,
            "always_active": self.always_active,
            "resources": self.resources,
        }


class SkillCatalog:
    """Discovers Thursday skill packages from SKILL.md frontmatter."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def specs(self) -> list[SkillSpec]:
        if not self.skills_dir.exists():
            return []
        specs: list[SkillSpec] = []
        for skill_dir in sorted(path for path in self.skills_dir.iterdir() if path.is_dir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            specs.append(self.read_spec(skill_file))
        return sorted(specs, key=lambda spec: (not spec.always_active, spec.name))

    def loadable_specs(self) -> list[SkillSpec]:
        return [spec for spec in self.specs() if not spec.always_active]

    def names(self) -> list[str]:
        return [spec.name for spec in self.loadable_specs()]

    def catalog(self) -> list[dict[str, Any]]:
        return [spec.to_catalog_item() for spec in self.specs()]

    def catalog_markdown(self) -> str:
        lines = [
            "[Thursday Skill Catalog]",
            "",
            "Available skill metadata from SKILL.md frontmatter. Each description states what the skill does and when to use it:",
        ]
        for spec in self.specs():
            suffix = " (always active)" if spec.always_active else ""
            lines.append(f"- `{spec.name}`{suffix}: {spec.description}")
            lines.append(f"  What it does: {spec.what_it_does}")
            lines.append(f"  When to use: {spec.when_to_use}")
            if spec.resources:
                lines.append(f"  Resources: {', '.join(f'`{item}`' for item in spec.resources)}")
        return "\n".join(lines)

    def get(self, skill_name: str) -> SkillSpec | None:
        requested = self.normalize_name(skill_name)
        for spec in self.specs():
            if spec.name == requested:
                return spec
        return None

    def read_spec(self, skill_file: Path) -> SkillSpec:
        self.ensure_inside_skills(skill_file)
        raw = skill_file.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(raw)
        name = str(metadata.get("name") or "").strip()
        what_it_does = str(metadata.get("what_it_does") or "").strip()
        when_to_use = str(metadata.get("when_to_use") or "").strip()
        description = str(metadata.get("description") or "").strip()
        if not SKILL_NAME_RE.match(name):
            raise ValueError(f"Invalid skill name in {skill_file}: {name!r}")
        validate_skill_description(skill_file, what_it_does, when_to_use, description)
        return SkillSpec(
            name=name,
            what_it_does=what_it_does,
            when_to_use=when_to_use,
            description=description,
            path=skill_file,
            body=body.strip(),
            resources=self.resources_for(skill_file.parent),
            always_active=name == "tool_operations",
        )

    def read_resource(self, skill_name: str, resource_path: str) -> dict[str, Any]:
        spec = self.get(skill_name)
        if not spec:
            raise ValueError(f"Unknown skill: {skill_name}")
        normalized = normalize_resource_path(resource_path)
        target = (spec.path.parent / normalized).resolve()
        root = spec.path.parent.resolve()
        if root != target and root not in target.parents:
            raise ValueError("Skill resource path escapes the skill package")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Skill resource not found: {normalized}")
        result: dict[str, Any] = {
            "skill_name": spec.name,
            "resource_path": normalized,
            "absolute_path": str(target),
            "bytes": target.stat().st_size,
        }
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result["content"] = ""
            result["binary"] = True
            result["message"] = "Resource is binary; use the absolute_path with an appropriate tool instead of loading bytes into context."
        else:
            result["content"] = content
            result["chars"] = len(content)
            result["binary"] = False
        return result

    def resources_for(self, skill_dir: Path) -> list[str]:
        resources: list[str] = []
        for root_name in sorted(RESOURCE_ROOTS):
            root = skill_dir / root_name
            if not root.exists() or not root.is_dir():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                rel = path.relative_to(skill_dir).as_posix()
                resources.append(rel)
        return resources

    def ensure_inside_skills(self, path: Path) -> None:
        resolved_dir = self.skills_dir.resolve()
        resolved_path = path.resolve()
        if resolved_dir != resolved_path and resolved_dir not in resolved_path.parents:
            raise ValueError(f"Path escapes skills directory: {path}")

    def normalize_name(self, skill_name: str) -> str:
        return str(skill_name or "").strip().lower().replace("-", "_")


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    text = raw.lstrip("\ufeff")
    if not text.startswith("---\n"):
        raise ValueError("Skill file must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("Skill frontmatter is not closed")
    frontmatter = text[4:end].strip()
    body = text[end + len("\n---") :].lstrip("\r\n")
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ValueError(f"Invalid frontmatter line: {line}")
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, body


def validate_skill_description(
    skill_file: Path,
    what_it_does: str,
    when_to_use: str,
    description: str,
) -> None:
    if not what_it_does:
        raise ValueError(f"Missing skill what_it_does in {skill_file}")
    if not when_to_use:
        raise ValueError(f"Missing skill when_to_use in {skill_file}")
    if not description:
        raise ValueError(f"Missing skill description in {skill_file}")
    expected_description = build_description(what_it_does, when_to_use)
    if description != expected_description:
        raise ValueError(
            f"Skill description in {skill_file} must be exactly: {expected_description!r}"
        )


def build_description(what_it_does: str, when_to_use: str) -> str:
    return f"What it does: {what_it_does} When to use: {when_to_use}"


def normalize_resource_path(resource_path: str) -> str:
    normalized = str(resource_path or "").replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Skill resource path must be relative and stay inside the skill package")
    if len(path.parts) < 2 or path.parts[0] not in RESOURCE_ROOTS:
        raise ValueError("Skill resource path must start with references/, scripts/, or assets/")
    return path.as_posix()
