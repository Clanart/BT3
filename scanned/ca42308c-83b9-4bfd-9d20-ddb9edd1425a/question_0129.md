# Q129: NEAR relayer fast-claim coupling replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public `claim_fee` plus earlier fast-finalization path` and make `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` either bypass replay protection or consume it for the wrong event because of uses `origin_transfer_id` to ensure that a relayer who fronted a fast transfer can only collect fee after the origin leg really finalizes with matching parameters, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
