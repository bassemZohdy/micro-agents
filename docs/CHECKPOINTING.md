# Checkpointing

Checkpointing is a runtime capability, not a promise that every execution point can be replayed safely. The built-in runtime advertises `checkpointing=true` only when a `CheckpointStore` is configured. The executable bootstrap derives that store from configured session persistence; therefore durability follows the session provider: memory is process-local, SQLite survives local restarts, and Redis can be shared by replicas.

The checkpoint id for a normal invocation is its `request_id`. Callers that need failure recovery should provide a stable `request_id`. Resume by invoking again with `checkpoint_id` set to that original id and an empty `input`; the stored input and transcript are authoritative. `checkpoint_id` and approval `continuation_id` are mutually exclusive. Unknown, expired, cross-tenant, or foreign-agent checkpoints fail with the stable HTTP `checkpoint_not_found` contract.

Checkpoints contain the exact runtime transcript, accumulated tool results, iteration counter, usage, session linkage, and input needed to continue immediately before a model call. Treat the checkpoint store as sensitive execution state and protect it to the same standard as session data.

## Replay safety

The runtime persists checkpoints only at replay-safe model-call boundaries. Before a tool wave containing a declared non-read-only side effect executes, the preceding checkpoint is deleted. A fresh checkpoint is written only after the complete tool wave succeeds and its results have been appended to the transcript. Therefore a crash during a side-effecting tool call deliberately has no resumable checkpoint rather than offering an unsafe replay path. Existing idempotency controls remain complementary; checkpointing does not replace operation-level idempotency.

The Google ADK adapter enables ADK resumability when a `CheckpointStore` is
configured. On a failed invocation it stores the ADK session events and state
alongside the runtime-neutral transcript; a later request with
`checkpoint_id` restores that snapshot and resumes the original ADK
invocation. Pending non-read-only tool calls invalidate the snapshot because
ADK resumption is at-least-once for tools. The executable bootstrap enables
process-local checkpointing when Google ADK uses the `memory` session mode;
SQLite and remote session bindings remain a separate adapter gap.

A successful invocation deletes its checkpoint. A runtime advertises
`checkpointing=true` only when the configured store and corresponding resume
implementation are available.
