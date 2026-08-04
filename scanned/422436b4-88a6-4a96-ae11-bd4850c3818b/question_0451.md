# Q451: getBlock downstream queue coupling

## Question
Can an unprivileged attacker use `getBlock` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_block` triggers a path where one request here triggers enough downstream work to inflate unrelated queues or watchers, violating the invariant that one request should not explode unrelated internal work queues and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_block
- Entrypoint: JSON-RPC `getBlock` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: trace the queues behind the API, not just the immediate method body
- Invariant to test: one request should not explode unrelated internal work queues
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: instrument downstream queue lengths while replaying one heavy call shape
