from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


_UNKNOWN_MODEL_NAMES = {"", "unknown", "unknown_model"}


def model_usage_display_name(value: Any) -> str:
    model = str(value or "").strip()
    if model.lower() in _UNKNOWN_MODEL_NAMES:
        return "unknown"
    return model


def model_usage_alias_key(value: Any) -> str:
    model = model_usage_display_name(value).lower()
    return _unqualified_model_name(model)


def aggregate_token_usage_by_model(
    entries: Iterable[tuple[Any, Any]],
    *,
    preferred_models: Iterable[Any] = (),
) -> dict[str, int]:
    """Aggregate token usage while folding safe provider/bare model aliases."""
    normalized_entries: list[tuple[str, int]] = []
    prefixed_by_unqualified: dict[str, dict[str, str]] = defaultdict(dict)
    unprefixed_by_name: set[str] = set()
    for model, tokens in entries:
        try:
            normalized_tokens = max(int(tokens or 0), 0)
        except (TypeError, ValueError):
            continue
        if normalized_tokens <= 0:
            continue
        display = model_usage_display_name(model)
        normalized_entries.append((display, normalized_tokens))
        key = _normalized_model_key(display)
        unqualified = _unqualified_model_name(key)
        if "/" in key:
            prefixed_by_unqualified[unqualified].setdefault(key, display)
        else:
            unprefixed_by_name.add(unqualified)

    preferred_by_exact: dict[str, str] = {}
    preferred_variants_by_unqualified: dict[str, dict[str, str]] = defaultdict(dict)
    for model in preferred_models:
        display = model_usage_display_name(model)
        if display == "unknown":
            continue
        exact = _normalized_model_key(display)
        preferred_by_exact.setdefault(exact, display)
        preferred_variants_by_unqualified[_unqualified_model_name(exact)].setdefault(exact, display)
    preferred_by_unqualified = {
        unqualified: next(iter(variants.values()))
        for unqualified, variants in preferred_variants_by_unqualified.items()
        if len(variants) == 1
    }

    usage_by_display: dict[str, int] = {}
    display_by_group: dict[str, str] = {}
    for display, normalized_tokens in normalized_entries:
        group, candidate = _model_usage_group(
            display,
            preferred_by_exact=preferred_by_exact,
            preferred_by_unqualified=preferred_by_unqualified,
            prefixed_by_unqualified=prefixed_by_unqualified,
            unprefixed_by_name=unprefixed_by_name,
        )
        existing = display_by_group.get(group)
        if existing is None:
            display_by_group[group] = candidate
            usage_by_display[candidate] = normalized_tokens
            continue

        chosen = _preferred_display_name(existing, candidate)
        if chosen != existing:
            usage_by_display[chosen] = usage_by_display.pop(existing, 0)
            display_by_group[group] = chosen
        usage_by_display[chosen] = usage_by_display.get(chosen, 0) + normalized_tokens

    return dict(sorted(usage_by_display.items()))


def _model_usage_group(
    display: str,
    *,
    preferred_by_exact: dict[str, str],
    preferred_by_unqualified: dict[str, str],
    prefixed_by_unqualified: dict[str, dict[str, str]],
    unprefixed_by_name: set[str],
) -> tuple[str, str]:
    exact = _normalized_model_key(display)
    unqualified = _unqualified_model_name(exact)
    preferred = preferred_by_exact.get(exact)
    if preferred:
        return f"preferred:{_normalized_model_key(preferred)}", preferred

    preferred = preferred_by_unqualified.get(unqualified)
    preferred_is_prefixed = "/" in _normalized_model_key(preferred) if preferred else False
    if preferred and (
        "/" not in exact
        or (not preferred_is_prefixed and len(prefixed_by_unqualified.get(unqualified, {})) <= 1)
    ):
        return f"preferred:{_normalized_model_key(preferred)}", preferred

    prefixed_variants = prefixed_by_unqualified.get(unqualified, {})
    has_unprefixed_variant = unqualified in unprefixed_by_name
    if has_unprefixed_variant and len(prefixed_variants) == 1:
        prefixed_display = next(iter(prefixed_variants.values()))
        candidate = display if "/" in exact else prefixed_display
        return f"alias:{unqualified}", candidate

    return f"exact:{exact}", display


def _preferred_display_name(existing: str, candidate: str) -> str:
    if "/" in existing and "/" not in candidate:
        return existing
    if "/" in candidate and "/" not in existing:
        return candidate
    return existing


def _unqualified_model_name(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def _normalized_model_key(model: str) -> str:
    return model.strip().lower()
