# Q417: simulateTransaction heap retention after send

## Question
Can an unprivileged attacker use `simulateTransaction` within the single-client low-rate model and choose serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags such that `simulate_transaction` triggers a path where large intermediate objects may survive longer than response emission or websocket flush, violating the invariant that transient request state should be released promptly after response emission and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::simulate_transaction
- Entrypoint: JSON-RPC `simulateTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags
- Exploit idea: treat allocator lifetime as the bug class rather than raw allocation size
- Invariant to test: transient request state should be released promptly after response emission
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: record heap over time during repeated heavy calls
