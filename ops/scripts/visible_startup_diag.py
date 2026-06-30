from __future__ import annotations


def emit_visible_startup_summary(stage: str, action: str, reason: str) -> None:
    print("::endgroup::")
    print("===== Visible gateway startup failure =====")
    print(f"VISIBLE_STARTUP_STAGE={stage}")
    print(f"VISIBLE_STARTUP_ACTION={action}")
    print(f"VISIBLE_STARTUP_REASON={reason}")
