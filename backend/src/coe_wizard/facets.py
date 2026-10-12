"""Facet matching between tagged nodes/chunks and a project's interview answers.

A facet is an interview choice question (its id) and its values are that question's option values.
"""

from collections.abc import Mapping, Sequence

from .ontology.model import Facets


def union(tag_sets: Sequence[Mapping[str, Sequence[str]]]) -> Facets:
    out: dict[str, list[str]] = {}
    for tags in tag_sets:
        for facet, values in tags.items():
            have = out.setdefault(facet, [])
            have.extend(v for v in values if v not in have)
    return {k: v for k, v in out.items() if v}


def matches(tags: Mapping[str, Sequence[str]], answers: Mapping[str, Sequence[str]]) -> bool:
    """True if every dimension both sides have intersects (AND across dimensions, OR within one).

    Dimensions the project didn't answer are ignored, so a tagged item with no overlapping
    dimension at all matches only if it shares at least one answered dimension.
    """
    shared = [f for f, values in tags.items() if values and answers.get(f)]
    if not shared:
        return False
    return all(set(tags[f]) & set(answers[f]) for f in shared)


def is_universal(tags: Mapping[str, Sequence[str]]) -> bool:
    return not any(tags.values())
