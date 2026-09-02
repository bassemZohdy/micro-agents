from pathlib import Path

workflow = Path('.github/workflows/temporary-runtime-checkpointing-runner.yml').read_text()
start_marker = "          python - <<'PY'\n"
end_marker = "\n          PY\n      - name: Install development dependencies"
start = workflow.index(start_marker) + len(start_marker)
end = workflow.index(end_marker, start)
raw_lines = workflow[start:end].splitlines()
lines: list[str] = []
in_triple = False
for line in raw_lines:
    processed = line if in_triple else (line[10:] if line.startswith("          ") else line)
    lines.append(processed)
    if processed.count("'''") % 2:
        in_triple = not in_triple
script = "\n".join(lines)
exec(compile(script, "checkpointing-implementation", "exec"))

path = Path("runtimes/adk/runtime.py")
text = path.read_text()
approval_save = '''                await self._approval_store.save(approval)
                raise _ApprovalPausedError(approval)
'''
approval_safe = '''                await self._approval_store.save(approval)
                if checkpoint_store is not None:
                    await deadline.run(
                        checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
                    )
                raise _ApprovalPausedError(approval)
'''
if approval_save not in text:
    raise RuntimeError("approval save boundary not found")
path.write_text(text.replace(approval_save, approval_safe, 1))

http_path = Path("micro_agent/interoperability/http_api.py")
http_text = http_path.read_text()
old_description = (
    '        description="Replay-safe checkpoint id to resume; '
    'do not combine with approval continuation.",\n'
)
new_description = (
    '        description=(\n'
    '            "Replay-safe checkpoint id to resume; do not combine with "\n'
    '            "approval continuation."\n'
    '        ),\n'
)
http_path.write_text(http_text.replace(old_description, new_description, 1))

todo = Path("TODO.md")
text = todo.read_text()
old_parent = (
    "- [ ] Add structured output, streaming, and checkpointing only behind truthful\n"
    "      runtime capabilities."
)
new_parent = (
    "- [x] Add structured output, streaming, and checkpointing only behind truthful\n"
    "      runtime capabilities."
)
todo.write_text(text.replace(old_parent, new_parent, 1))
