# Q480: getBlocks result cloning chain

## Question
Can an unprivileged attacker use `getBlocks` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_blocks` triggers a path where the same large result may be cloned multiple times along the way to the caller, violating the invariant that large results should not be redundantly cloned in proportion to pipeline stages and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_blocks
- Entrypoint: JSON-RPC `getBlocks` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: look for repeated copies in the hot path
- Invariant to test: large results should not be redundantly cloned in proportion to pipeline stages
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: profile allocations and clone counts on a single heavy request
