# Q517: getTransaction slow-path lock contention

## Question
Can an unprivileged attacker use `getTransaction` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_transaction` triggers a path where this method may hold a shared lock or guard long enough to degrade other request classes, violating the invariant that one heavy request must not monopolize shared locks at low rate and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_transaction
- Entrypoint: JSON-RPC `getTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: treat lock hold time as the exploit surface
- Invariant to test: one heavy request must not monopolize shared locks at low rate
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: trace lock hold times during boundary requests
