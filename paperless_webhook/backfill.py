#!/usr/bin/env python3
"""
backfill.py — Batch-translate existing Paperless-ngx documents.

Iterates over a range of document IDs, applies the same guards as the webhook
(skips companions, skips already-translated, checks source language), and
translates qualifying documents via pdf-translate.

Usage:
    # Preview what would be translated (no changes):
    python backfill.py --start 1 --end 50 --dry-run

    # Translate all matching docs in range:
    python backfill.py --start 1 --end 50

    # Slower pace to avoid overwhelming pdf-translate:
    python backfill.py --start 1 --end 800 --delay 5

    # Via Docker (same image as the webhook):
    docker run --rm \\
      -e PAPERLESS_URL=http://paperless-ngx:8000 \\
      -e PAPERLESS_API_TOKEN=<token> \\
      -e PDF_TRANSLATE_URL=http://<host>:7860 \\
      -e LIBRETRANSLATE_URL=http://<lt-host>:5000 \\
      -e TRANSLATE_SOURCE_LANG=de \\
      -e TRANSLATE_TARGET_LANG=en \\
      -e PDF_TRANSLATE_MERGE_BLOCKS=true \\
      ghcr.io/jctots/pdf-translate-paperless-webhook:latest \\
      python backfill.py --start 1 --end 50 --dry-run

Environment variables: same as the webhook container. See README.md.
"""

import argparse
import sys
import time

import httpx

import webhook
from webhook import (
    FIELD_TRANSLATION,
    SOURCE_LANG,
    TAG_AUTO_TRANSLATED,
    detect_language,
    get_custom_field_ids,
    get_document,
    get_tag_id_by_name,
    logger,
)

# ---------------------------------------------------------------------------
# Per-document eligibility check
# ---------------------------------------------------------------------------

def _check(
    client: httpx.Client,
    doc_id: int,
    auto_tag_id: int | None,
    translation_field_id: int | None,
) -> tuple[str, str]:
    """
    Determine whether doc_id should be translated.

    Returns (status, detail) where status is one of:
      "translate"   — eligible; detail is the document title
      "skip"        — not eligible; detail explains why
      "not_found"   — document does not exist (ID gap)
      "error"       — unexpected API error; detail is the error message
    """
    try:
        doc = get_document(client, doc_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "not_found", "404"
        return "error", f"HTTP {exc.response.status_code}"
    except Exception as exc:
        return "error", str(exc)

    # Guard: skip auto-translated companions
    if auto_tag_id is not None and auto_tag_id in doc.get("tags", []):
        return "skip", "auto-translated companion"

    # Guard: skip originals that already have a translation linked
    if translation_field_id is not None and any(
        f.get("field") == translation_field_id
        for f in doc.get("custom_fields", [])
    ):
        return "skip", "already translated"

    # Guard: language filter (only when SOURCE_LANG is not "auto")
    if SOURCE_LANG != "auto":
        content = doc.get("content", "")
        detected = detect_language(content)
        if detected is not None and detected != SOURCE_LANG:
            return "skip", f"lang={detected} (want {SOURCE_LANG})"

    return "translate", doc.get("title", "Untitled")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(start: int, end: int, dry_run: bool, delay: float) -> None:
    total = end - start + 1
    counts = {"translated": 0, "skipped": 0, "not_found": 0, "error": 0}

    print(f"\npdf-translate backfill — IDs {start}–{end} ({total} to check)")
    if dry_run:
        print("DRY RUN — no translations will be performed\n")

    # Fetch shared lookups once to avoid N extra API calls
    with httpx.Client() as probe:
        auto_tag_id = get_tag_id_by_name(probe, TAG_AUTO_TRANSLATED)
        try:
            field_ids = get_custom_field_ids(probe)
            translation_field_id = field_ids.get(FIELD_TRANSLATION)
        except Exception:
            translation_field_id = None

    logger.info(
        "backfill: auto_tag_id=%s translation_field_id=%s source_lang=%s dry_run=%s",
        auto_tag_id, translation_field_id, SOURCE_LANG, dry_run,
    )

    to_translate: list[tuple[int, str]] = []

    # ---- Phase 1: scan ----
    with httpx.Client() as client:
        for doc_id in range(start, end + 1):
            status, detail = _check(client, doc_id, auto_tag_id, translation_field_id)

            if status == "not_found":
                counts["not_found"] += 1
                # Silent — gaps in Paperless IDs are expected
            elif status == "skip":
                counts["skipped"] += 1
                print(f"  · [{doc_id:6d}] skip: {detail}")
            elif status == "error":
                counts["error"] += 1
                print(f"  ! [{doc_id:6d}] error: {detail}")
            else:  # translate
                to_translate.append((doc_id, detail))
                print(f"  → [{doc_id:6d}] {detail!r}")

    print(
        f"\nScan complete: {len(to_translate)} to translate, "
        f"{counts['skipped']} skipped, "
        f"{counts['not_found']} not found, "
        f"{counts['error']} errors"
    )

    if dry_run or not to_translate:
        if dry_run:
            print("\nDry run — remove --dry-run to translate.")
        else:
            print("\nNothing to translate.")
        return

    # ---- Phase 2: translate ----
    print()
    for i, (doc_id, title) in enumerate(to_translate, 1):
        print(f"[{i}/{len(to_translate)}] doc {doc_id}: {title!r} ...", end=" ", flush=True)

        emitted: list[dict] = []
        original_emit = webhook.emit
        webhook.emit = lambda e: emitted.append(e)  # noqa: B023
        try:
            webhook.handle(doc_id, None)
        finally:
            webhook.emit = original_emit

        if emitted:
            entry = emitted[-1]
            action = entry.get("action", "?")
            if action == "translated":
                companions = ", ".join(u["title"] for u in entry.get("uploaded", []))
                errs = entry.get("errors", [])
                suffix = f" (link errors: {errs})" if errs else ""
                print(f"✓  → {companions}{suffix}")
                counts["translated"] += 1
            elif action == "skipped":
                print(f"· skipped ({entry.get('reason', '?')})")
                counts["skipped"] += 1
            else:
                reason = entry.get("reason", "?")
                print(f"✗ {action}: {reason}")
                counts["error"] += 1
        else:
            print("? no emit (unexpected)")
            counts["error"] += 1

        if i < len(to_translate) and delay > 0:
            time.sleep(delay)

    print(
        f"\nDone. translated={counts['translated']} "
        f"skipped={counts['skipped']} "
        f"errors={counts['error']}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-translate existing Paperless-ngx documents via pdf-translate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--start", type=int, required=True,
        help="First document ID to check (inclusive)",
    )
    parser.add_argument(
        "--end", type=int, required=True,
        help="Last document ID to check (inclusive)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report eligibility without translating",
    )
    parser.add_argument(
        "--delay", type=float, default=2.0, metavar="SECONDS",
        help="Pause between translations to avoid overwhelming pdf-translate (default: 2.0)",
    )
    args = parser.parse_args()

    if args.start > args.end:
        print(
            f"error: --start ({args.start}) must be ≤ --end ({args.end})",
            file=sys.stderr,
        )
        sys.exit(1)

    run(args.start, args.end, args.dry_run, args.delay)


if __name__ == "__main__":
    main()
