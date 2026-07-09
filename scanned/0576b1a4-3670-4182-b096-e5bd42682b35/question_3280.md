# Q3280: NEAR relayer fast-claim coupling shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public `claim_fee` plus earlier fast-finalization path` because `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids` trusts the same response envelope under a different meaning, violating `the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback with fast-transfer origin ids`
- Entrypoint: `public `claim_fee` plus earlier fast-finalization path`
- Attacker controls: fast-transfer id, origin transfer id, relayer identity, fee recipient, and settlement order across both legs
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: the first leg and second leg of a fast transfer must stay tightly coupled so a relayer cannot claim against a different transfer or a different fee schedule
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
