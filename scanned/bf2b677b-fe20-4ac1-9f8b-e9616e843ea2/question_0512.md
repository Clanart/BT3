# Q512: getTransaction heap retention after send

## Question
Can an unprivileged attacker use `getTransaction` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_transaction` triggers a path where large intermediate objects may survive longer than response emission or websocket flush, violating the invariant that transient request state should be released promptly after response emission and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_transaction
- Entrypoint: JSON-RPC `getTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: treat allocator lifetime as the bug class rather than raw allocation size
- Invariant to test: transient request state should be released promptly after response emission
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: record heap over time during repeated heavy calls
