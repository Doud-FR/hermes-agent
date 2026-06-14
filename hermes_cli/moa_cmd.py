"""CLI helpers for configuring Mixture of Agents."""

from __future__ import annotations

from typing import Any

from hermes_cli.config import load_config, save_config
from hermes_cli.inventory import build_models_payload, load_picker_context
from hermes_cli.moa_config import normalize_moa_config


def _prompt_choice(title: str, rows: list[str], default: int = 0) -> int:
    try:
        from hermes_cli.curses_ui import curses_radiolist

        return curses_radiolist(title, rows, selected=default, cancel_returns=default)
    except Exception:
        for idx, row in enumerate(rows, start=1):
            print(f"{idx}. {row}")
        raw = input(f"{title} [{default + 1}]: ").strip()
        if not raw:
            return default
        try:
            return max(0, min(len(rows) - 1, int(raw) - 1))
        except ValueError:
            return default


def _model_options() -> list[dict[str, Any]]:
    payload = build_models_payload(
        load_picker_context(),
        include_unconfigured=True,
        picker_hints=True,
        canonical_order=True,
        pricing=True,
        capabilities=True,
        max_models=200,
    )
    providers = payload.get("providers") or []
    return [p for p in providers if p.get("slug") and p.get("models")]


def _pick_slot(current: dict[str, str] | None = None) -> dict[str, str]:
    providers = _model_options()
    if not providers:
        raise RuntimeError("No configured model providers found. Run `hermes model` first.")
    current_provider = (current or {}).get("provider", "")
    provider_default = next(
        (idx for idx, p in enumerate(providers) if p.get("slug") == current_provider),
        0,
    )
    provider_rows = [f"{p.get('name') or p.get('slug')}  ({p.get('slug')})" for p in providers]
    provider = providers[_prompt_choice("Select provider", provider_rows, provider_default)]
    models = list(provider.get("models") or [])
    if not models:
        raise RuntimeError(f"Provider {provider.get('slug')} has no selectable models")
    current_model = (current or {}).get("model", "")
    model_default = models.index(current_model) if current_model in models else 0
    model = models[_prompt_choice(f"Select model for {provider.get('slug')}", models, model_default)]
    return {"provider": str(provider.get("slug") or ""), "model": str(model)}


def _print_config(config: dict[str, Any]) -> None:
    cfg = normalize_moa_config(config.get("moa") if isinstance(config, dict) else {})
    print("Mixture of Agents")
    print("Reference models:")
    for idx, slot in enumerate(cfg["reference_models"], start=1):
        print(f"  {idx}. {slot['provider']}:{slot['model']}")
    agg = cfg["aggregator"]
    print(f"Aggregator: {agg['provider']}:{agg['model']}")


def cmd_moa(args) -> None:
    """Manage Mixture of Agents model slots."""
    cfg = load_config()
    sub = getattr(args, "moa_command", None) or "list"

    if sub in {"list", "ls"}:
        _print_config(cfg)
        return

    if sub in {"config", "configure"}:
        moa = normalize_moa_config(cfg.get("moa") if isinstance(cfg, dict) else {})
        print("Configure reference models. Pick at least one; choose Done when finished.")
        refs: list[dict[str, str]] = []
        existing = list(moa.get("reference_models") or [])
        idx = 0
        while True:
            base = existing[idx] if idx < len(existing) else None
            refs.append(_pick_slot(base))
            idx += 1
            choice = _prompt_choice("Add another reference model?", ["Add another", "Done"], 1)
            if choice == 1:
                break
        print("Configure aggregator model.")
        moa["reference_models"] = refs
        moa["aggregator"] = _pick_slot(moa.get("aggregator"))
        cfg["moa"] = normalize_moa_config(moa)
        save_config(cfg)
        print("Saved MoA configuration.")
        _print_config(cfg)
        return

    raise SystemExit(f"Unknown moa subcommand: {sub}")
