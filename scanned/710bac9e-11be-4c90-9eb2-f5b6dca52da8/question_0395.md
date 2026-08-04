# Q395: sendTransaction shared-executor fairness

## Question
Can an unprivileged attacker use `sendTransaction` within the single-client low-rate model and choose serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags such that `send_transaction` triggers a path where a single client can make this method occupy shared async worker capacity long enough to measurably degrade unrelated cheap methods, violating the invariant that rpc worker time should remain fairly shareable under one-client use and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::send_transaction
- Entrypoint: JSON-RPC `sendTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags
- Exploit idea: focus on cross-method impact, not just local latency
- Invariant to test: RPC worker time should remain fairly shareable under one-client use
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run a cheap control RPC in parallel and quantify latency inflation
