# Q533: getSignaturesForAddress downstream queue coupling

## Question
Can an unprivileged attacker use `getSignaturesForAddress` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_signatures_for_address` triggers a path where one request here triggers enough downstream work to inflate unrelated queues or watchers, violating the invariant that one request should not explode unrelated internal work queues and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_signatures_for_address
- Entrypoint: JSON-RPC `getSignaturesForAddress` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: trace the queues behind the API, not just the immediate method body
- Invariant to test: one request should not explode unrelated internal work queues
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: instrument downstream queue lengths while replaying one heavy call shape
