# Q3550: NEAR relayer fast-claim coupling shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `public `claim_fee` plus earlier fast-finalization path` with control over fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs and desynchronize `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because uses `origin_transfer_id` to ensure that a relayer who fronted a fast transfer can only collect fee after the origin leg really finalizes with matching parameters, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` and the adjacent replay-protection bookkeeping after every branch.
