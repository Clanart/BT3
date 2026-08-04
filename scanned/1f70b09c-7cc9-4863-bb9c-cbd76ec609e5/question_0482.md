# Q482: getBlocks stable low-rate degradation

## Question
Can an unprivileged attacker use `getBlocks` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_blocks` triggers a path where the method may still remain exploitable even when driven strictly within the program’s low-rate RPC DoS rules, violating the invariant that the allowed low-rate single-client model should remain service-safe and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_blocks
- Entrypoint: JSON-RPC `getBlocks` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: keep the attacker model within the stated Immunefi allowance and see whether the node still degrades materially
- Invariant to test: the allowed low-rate single-client model should remain service-safe
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: replay one request every `CLUSTER_SLOT_TIME_TARGET / 2` and measure whether latency drifts upward
