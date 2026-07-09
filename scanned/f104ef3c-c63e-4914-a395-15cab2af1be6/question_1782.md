# Q1782: NEAR relayer fast-claim coupling final settlement and later fee claim can diverge through cross-module drift

## Question
Can an unprivileged attacker use `public `claim_fee` plus earlier fast-finalization path` with control over fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs and desynchronize `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `final settlement and later fee claim can diverge` attack class because uses `origin_transfer_id` to ensure that a relayer who fronted a fast transfer can only collect fee after the origin leg really finalizes with matching parameters, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` and the adjacent replay-protection bookkeeping after every branch.
