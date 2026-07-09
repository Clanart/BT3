# Q2703: NEAR relayer fast-claim coupling stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public `claim_fee` plus earlier fast-finalization path` and make `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` treat it as fresh because of uses `origin_transfer_id` to ensure that a relayer who fronted a fast transfer can only collect fee after the origin leg really finalizes with matching parameters, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
