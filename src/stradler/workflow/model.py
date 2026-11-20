"""Workflow model: the typed form of a validated recipe (WORKFLOWS.md section 3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .identity import digest_of

__all__ = ["Container", "InputsBlock", "Pin", "Recipe", "Select", "Step", "StepInput", "StepOutput"]


@dataclass(frozen=True)
class Pin:
    """The image a run must use.

    Attributes
    ----------
    version : str
        Version label, recorded not checked.
    sha256 : str
        Lowercase 64-digit hexadecimal digest of the image file.
    path : str or None
        Project-relative path of the image file.
    """

    version: str
    sha256: str
    path: str | None = None

    @property
    def placeholder(self) -> bool:
        """Whether the pin is the all-zero placeholder."""
        return set(self.sha256) == {"0"}


@dataclass(frozen=True)
class Container:
    """A container entry.

    Attributes
    ----------
    name : str
        Container name.
    kind : str
        ``recipe`` or ``image``.
    recipe, image : str or None
        Container recipe path, or image reference.
    pin : Pin
        Pinned image.
    """

    name: str
    kind: str
    recipe: str | None
    image: str | None
    pin: Pin


@dataclass(frozen=True)
class Select:
    """The ``select`` mapping of a step input (section 3.6.1).

    Attributes
    ----------
    for_each : str or None
        Level to aggregate over.
    pick : str or None
        Level of the first child to take (from ``first_<level>``).
    exists : str or None
        Output name that must exist for the binding to be kept.
    """

    for_each: str | None = None
    pick: str | None = None
    exists: str | None = None


@dataclass(frozen=True)
class StepInput:
    """One entry of a step's ``in`` list (section 3.6).

    Attributes
    ----------
    as_name : str or None
        Placeholder name; ``None`` in an ``else`` entry.
    source : str
        ``inputs``, ``fixed``, ``named`` or the name of an earlier step.
    path, name : str or None
        Path of a ``fixed`` input; key of a ``named`` input.
    select : Select or None
        Selection for step sources.
    else_ : StepInput or None
        Binding used when the ``select`` condition is false.
    """

    as_name: str | None
    source: str
    path: str | None = None
    name: str | None = None
    select: Select | None = None
    else_: StepInput | None = None


@dataclass(frozen=True)
class StepOutput:
    """One entry of a step's ``out`` list.

    Attributes
    ----------
    name : str
        Output name.
    path : str
        Project-relative path template.
    """

    name: str
    path: str


@dataclass(frozen=True)
class Step:
    """A step of a workflow.

    Attributes
    ----------
    name, purpose, level, container : str
        Identity, intent, unit level and container name (``none`` for no container).
    command : tuple of str
        Argument vector template.
    inputs : tuple of StepInput
    outputs : tuple of StepOutput
    partial : bool
        ``on_incomplete: partial`` was given.
    min_units : int
        Least upstream units of a partial join.
    resources : dict
        Scheduler hints (``cpus``, ``memory``, ``time``, ``gpus``).
    interpretation, code, stage_kind : str or None
        Optional step metadata; ``code`` is the declared or derived code.
    """

    name: str
    purpose: str
    level: str
    container: str
    command: tuple[str, ...]
    inputs: tuple[StepInput, ...]
    outputs: tuple[StepOutput, ...]
    partial: bool = False
    min_units: int = 1
    resources: Mapping[str, Any] = field(default_factory=dict)
    interpretation: str | None = None
    code: str | None = None
    stage_kind: str | None = None

    @property
    def is_join(self) -> bool:
        """Whether any input aggregates with ``for_each`` (section 6)."""
        return any(i.select is not None and i.select.for_each for i in self.inputs)


@dataclass(frozen=True)
class InputsBlock:
    """The ``inputs`` block (section 3.4).

    Attributes
    ----------
    dataset : str
        Project-relative dataset folder.
    filter : dict
        Selection of files, read by the data handler.
    units : dict
        Level name to ``{"include": [...], "exclude": [...]}``.
    """

    dataset: str
    filter: Mapping[str, Any] = field(default_factory=dict)
    units: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    """A validated workflow recipe.

    Attributes
    ----------
    name, family, version, purpose : str
        Identity and intent of the workflow.
    containers : dict of str to Container
    inputs : InputsBlock
    steps : tuple of Step
        Steps in recipe order.
    document : dict
        The recipe after schema conversion (integers converted), including ``schema``.
    """

    name: str
    family: str
    version: str
    purpose: str
    containers: Mapping[str, Container]
    inputs: InputsBlock
    steps: tuple[Step, ...]
    document: Mapping[str, Any]

    def step(self, name: str) -> Step:
        """Return the step called ``name``.

        Parameters
        ----------
        name : str
            Step name.

        Returns
        -------
        Step

        Raises
        ------
        KeyError
            If no step has that name.
        """
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def content_digest(self) -> str:
        """Digest of the canonical JSON of the converted document (section 10.4)."""
        return digest_of(self.document)

    @property
    def inputs_digest(self) -> str:
        """Digest of the canonical JSON of the converted ``inputs`` block (section 10.4)."""
        return digest_of(self.document["inputs"])

    @property
    def identity(self) -> tuple[str, str, str]:
        """``(name, family, version)``."""
        return (self.name, self.family, self.version)


def _build_input(d: Mapping[str, Any]) -> StepInput:
    sel = None
    if "select" in d:
        s = d["select"]
        pick = s.get("pick")
        sel = Select(
            for_each=s.get("for_each"),
            pick=pick[len("first_"):] if pick else None,
            exists=s.get("condition", {}).get("exists") if "condition" in s else None,
        )
    return StepInput(
        as_name=d.get("as"),
        source=d["from"],
        path=d.get("path"),
        name=d.get("name"),
        select=sel,
        else_=_build_input(d["else"]) if "else" in d else None,
    )


def build_recipe(doc: Mapping[str, Any], codes: Mapping[int, str]) -> Recipe:
    """Build the model from a validated, converted document.

    Parameters
    ----------
    doc : mapping
        Document that passed validation and conversion.
    codes : mapping
        Step index to the declared or derived step code.

    Returns
    -------
    Recipe
    """
    containers = {}
    for cname, c in (doc.get("containers") or {}).items():
        pin = c["pin"]
        containers[cname] = Container(
            name=cname,
            kind=c.get("kind", "recipe"),
            recipe=c.get("recipe"),
            image=c.get("image"),
            pin=Pin(pin["version"], pin["sha256"].lower(), pin.get("path")),
        )
    inp = doc["inputs"]
    steps = []
    for i, s in enumerate(doc["steps"]):
        steps.append(
            Step(
                name=s["name"],
                purpose=s["purpose"],
                level=s["level"],
                container=s["container"],
                command=tuple(s["command"]),
                inputs=tuple(_build_input(e) for e in s.get("in", [])),
                outputs=tuple(StepOutput(o["name"], o["path"]) for o in s["out"]),
                partial=s.get("on_incomplete") == "partial",
                min_units=s.get("min_units", 1),
                resources=dict(s.get("resources", {})),
                interpretation=s.get("interpretation"),
                code=codes.get(i),
                stage_kind=s.get("stage_kind"),
            )
        )
    return Recipe(
        name=doc["name"],
        family=doc["family"],
        version=doc["version"],
        purpose=doc["purpose"],
        containers=containers,
        inputs=InputsBlock(inp["dataset"], dict(inp.get("filter", {})), dict(inp.get("units", {}))),
        steps=tuple(steps),
        document=doc,
    )
