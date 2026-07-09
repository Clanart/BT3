# Q3190: NEAR claim_fee entry shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public `claim_fee` proof-submission flow` because `near/omni-bridge/src/lib.rs::claim_fee` trusts the same response envelope under a different meaning, violating `fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee`
- Entrypoint: `public `claim_fee` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and caller identity as purported fee recipient
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
