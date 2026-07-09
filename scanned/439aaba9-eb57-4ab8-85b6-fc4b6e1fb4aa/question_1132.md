# Q1132: NEAR relayer fast-claim coupling recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public `claim_fee` plus earlier fast-finalization path` with control over fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs and desynchronize `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because uses `origin_transfer_id` to ensure that a relayer who fronted a fast transfer can only collect fee after the origin leg really finalizes with matching parameters, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` and the adjacent replay-protection bookkeeping after every branch.
