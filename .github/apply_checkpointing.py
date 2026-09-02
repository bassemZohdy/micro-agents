from pathlib import Path

workflow = Path('.github/workflows/temporary-runtime-checkpointing-runner.yml').read_text()
start_marker = "          python - <<'PY'\n"
end_marker = "\n          PY\n      - name: Install development dependencies"
start = workflow.index(start_marker) + len(start_marker)
end = workflow.index(end_marker, start)
lines = workflow[start:end].splitlines()
script = "\n".join(line[10:] if line.startswith("          ") else line for line in lines)
exec(compile(script, 'checkpointing-implementation', 'exec'))

path = Path('runtimes/adk/runtime.py')
text = path.read_text()
misplaced = '''            if (
                checkpoint_store is not None
                and not self._tool_wave_is_replay_safe(agent, tools, response.tool_requests)
            ):
                await deadline.run(
                    checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
                )

            tool_results = await self._execute_tools(
                tools,
                resume.tool_requests,
'''
corrected_resume = '''            tool_results = await self._execute_tools(
                tools,
                resume.tool_requests,
'''
if misplaced not in text:
    raise RuntimeError('expected misplaced approval checkpoint guard not found')
text = text.replace(misplaced, corrected_resume, 1)

normal_call = '''            tool_results = await self._execute_tools(
                tools,
                response.tool_requests,
'''
normal_guard = '''            if (
                checkpoint_store is not None
                and not self._tool_wave_is_replay_safe(agent, tools, response.tool_requests)
            ):
                await deadline.run(
                    checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
                )

            tool_results = await self._execute_tools(
                tools,
                response.tool_requests,
'''
if normal_call not in text:
    raise RuntimeError('normal tool wave call not found')
text = text.replace(normal_call, normal_guard, 1)

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
    raise RuntimeError('approval save boundary not found')
text = text.replace(approval_save, approval_safe, 1)
path.write_text(text)

todo = Path('TODO.md')
text = todo.read_text()
text = text.replace(
    '- [ ] Add structured output, streaming, and checkpointing only behind truthful\n      runtime capabilities.',
    '- [x] Add structured output, streaming, and checkpointing only behind truthful\n      runtime capabilities.',
    1,
)
todo.write_text(text)
