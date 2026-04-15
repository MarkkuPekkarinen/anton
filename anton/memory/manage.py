from __future__ import annotations

from rich.console import Console

from anton.config.settings import AntonSettings



def handle_memory_cmd(
    cmd: str,
    console: Console,
    settings: AntonSettings,
    cortex: "Cortex | None",
    episodic: "EpisodicMemory | None" = None,
) -> None:
    """Show memory status — read-only dashboard."""
    console.print()
    console.print("[anton.cyan]Memory Status[/]")
    console.print()

    mode_labels = {
        "autopilot": "Autopilot — Anton decides what to remember",
        "copilot": "Co-pilot — save obvious, confirm ambiguous",
        "off": "Off — never save (still reads existing)",
    }
    mode_label = mode_labels.get(settings.memory_mode, settings.memory_mode)
    console.print(f"  Mode:  [bold]{mode_label}[/]")
    console.print()

    if cortex is None:
        console.print("  [anton.warning]Memory system not initialized.[/]")
        console.print()
        return

    def _show_scope(label: str, hc) -> int:
        identity = hc.recall_identity()
        rules = hc.recall_rules()
        lessons_raw = hc._read_full_lessons()
        rule_count = (
            sum(1 for ln in rules.splitlines() if ln.strip().startswith("- "))
            if rules
            else 0
        )
        lesson_count = (
            sum(1 for ln in lessons_raw.splitlines() if ln.strip().startswith("- "))
            if lessons_raw
            else 0
        )
        topics: list[str] = []
        if hc._topics_dir.is_dir():
            topics = [
                p.stem for p in sorted(hc._topics_dir.iterdir()) if p.suffix == ".md"
            ]

        console.print(f"  [anton.cyan]{label}[/] [dim]({hc._dir})[/]")
        if identity:
            entries = [
                ln.strip()[2:]
                for ln in identity.splitlines()
                if ln.strip().startswith("- ")
            ]
            if entries:
                console.print(
                    f"    Identity:  {', '.join(entries[:3])}"
                    + (" ..." if len(entries) > 3 else "")
                )
            else:
                console.print("    Identity:  [dim](set)[/]")
        else:
            console.print("    Identity:  [dim](empty)[/]")
        console.print(f"    Rules:     {rule_count}")
        console.print(f"    Lessons:   {lesson_count}")
        if topics:
            console.print(f"    Topics:    {', '.join(topics)}")
        else:
            console.print("    Topics:    [dim](none)[/]")
        console.print()
        return rule_count + lesson_count

    global_total = _show_scope("Global Memory", cortex.global_hc)
    project_total = _show_scope("Project Memory", cortex.project_hc)

    total = global_total + project_total
    console.print(f"  Total entries: [bold]{total}[/]")
    if cortex.needs_compaction():
        console.print("  [anton.warning]Compaction needed (>50 entries in a scope)[/]")
    console.print()

    if episodic is not None:
        status = "[bold]ON[/]" if episodic.enabled else "[dim]OFF[/]"
        sessions = episodic.session_count()
        console.print(f"  [anton.cyan]Episodic Memory[/]")
        console.print(f"    Status:    {status}")
        console.print(f"    Sessions:  {sessions}")
        console.print()

    console.print("[dim]  Use /setup > Memory to change configuration.[/]")
    console.print()
