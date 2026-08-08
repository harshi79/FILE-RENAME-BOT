"""
The rename engine.

Pure operations on filenames used by both single-file and batch flows.
Every transform preserves the original extension; extension replacement is
an explicit, separately-validated step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from core import filename as fn


@dataclass
class RenamePlan:
    """Describes how a single file should be renamed."""
    operation: str                         # rename | extension | prefix | ...
    original_name: str
    new_name: str
    params: Dict = field(default_factory=dict)


def _stem(original_name: str) -> str:
    stem, _ = fn.split_extension(original_name)
    return stem


def _build(original_name: str, new_stem: str, new_ext: Optional[str] = None) -> str:
    if new_ext is not None:
        return fn.apply_extension(original_name, new_ext)
    return fn.apply_stem(original_name, new_stem)


def plan_rename(original_name: str, user_input: str) -> RenamePlan:
    """Normal rename: user-supplied extension is ignored."""
    new_name = fn.apply_stem(original_name, user_input)
    return RenamePlan("rename", original_name, new_name, {"input": user_input})


def plan_extension(original_name: str, new_ext: str) -> RenamePlan:
    norm = fn.normalise_extension(new_ext)
    new_name = fn.apply_extension(original_name, norm)
    return RenamePlan("extension", original_name, new_name, {"ext": norm})


def plan_find_replace(original_name: str, find: str, replace: str) -> RenamePlan:
    stem = _stem(original_name)
    new_stem = fn.transform_find_replace(stem, find, replace)
    new_name = fn.apply_stem(original_name, new_stem)
    return RenamePlan("find_replace", original_name, new_name, {"find": find, "replace": replace})


def plan_prefix(original_name: str, prefix: str) -> RenamePlan:
    new_stem = fn.transform_prefix(_stem(original_name), prefix)
    return RenamePlan("prefix", original_name, fn.apply_stem(original_name, new_stem), {"prefix": prefix})


def plan_suffix(original_name: str, suffix: str) -> RenamePlan:
    new_stem = fn.transform_suffix(_stem(original_name), suffix)
    return RenamePlan("suffix", original_name, fn.apply_stem(original_name, new_stem), {"suffix": suffix})


def plan_remove_prefix(original_name: str, prefix: str) -> RenamePlan:
    new_stem = fn.transform_remove_prefix(_stem(original_name), prefix)
    return RenamePlan("remove_prefix", original_name, fn.apply_stem(original_name, new_stem), {"prefix": prefix})


def plan_remove_suffix(original_name: str, suffix: str) -> RenamePlan:
    new_stem = fn.transform_remove_suffix(_stem(original_name), suffix)
    return RenamePlan("remove_suffix", original_name, fn.apply_stem(original_name, new_stem), {"suffix": suffix})


def plan_whitespace(original_name: str) -> RenamePlan:
    new_stem = fn.transform_whitespace(_stem(original_name))
    return RenamePlan("whitespace", original_name, fn.apply_stem(original_name, new_stem))


def plan_case(original_name: str, mode: str) -> RenamePlan:
    new_stem = fn.transform_case(_stem(original_name), mode)
    return RenamePlan("case", original_name, fn.apply_stem(original_name, new_stem), {"mode": mode})


def plan_number(original_name: str, base: str, index: int, start: int = 1, pad: int = 2) -> RenamePlan:
    """Sequential numbering; ``index`` is 1-based position in the batch."""
    number = str(start + index - 1).zfill(max(1, pad))
    new_stem = f"{base} {number}".strip()
    return RenamePlan(
        "number",
        original_name,
        fn.apply_stem(original_name, new_stem),
        {"base": base, "index": index, "start": start, "pad": pad},
    )


# ──────────────────────────────────────────────────────────────────────
# Batch helpers – apply a single plan factory across many files.
# ──────────────────────────────────────────────────────────────────────
def build_batch_plans(
    original_names: List[str],
    factory: Callable[[str, int], RenamePlan],
) -> List[RenamePlan]:
    """
    ``factory`` receives (original_name, 1_based_index) and returns a plan.
    Callers can implement prefix/suffix/find-replace/numbering this way.
    """
    plans: List[RenamePlan] = []
    for idx, name in enumerate(original_names, start=1):
        plans.append(factory(name, idx))
    return plans


def find_replace_batch(originals: List[str], find: str, replace: str) -> List[RenamePlan]:
    return build_batch_plans(originals, lambda n, _i: plan_find_replace(n, find, replace))


def prefix_batch(originals: List[str], prefix: str) -> List[RenamePlan]:
    return build_batch_plans(originals, lambda n, _i: plan_prefix(n, prefix))


def suffix_batch(originals: List[str], suffix: str) -> List[RenamePlan]:
    return build_batch_plans(originals, lambda n, _i: plan_suffix(n, suffix))


def number_batch(originals: List[str], base: str, start: int = 1, pad: int = 2) -> List[RenamePlan]:
    return build_batch_plans(originals, lambda n, i: plan_number(n, base, i, start, pad))


def extension_batch(originals: List[str], new_ext: str) -> List[RenamePlan]:
    return build_batch_plans(originals, lambda n, _i: plan_extension(n, new_ext))
