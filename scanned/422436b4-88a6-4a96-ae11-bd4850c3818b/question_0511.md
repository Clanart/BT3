# Q511: getTransaction shared-executor fairness

## Question
Can an unprivileged attacker use `getTransaction` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_transaction` triggers a path where a single client can make this method occupy shared async worker capacity long enough to measurably degrade unrelated cheap methods, violating the invariant that rpc worker time should remain fairly shareable under one-client use and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_transaction
- Entrypoint: JSON-RPC `getTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: focus on cross-method impact, not just local latency
- Invariant to test: RPC worker time should remain fairly shareable under one-client use
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run a cheap control RPC in parallel and quantify latency inflation
