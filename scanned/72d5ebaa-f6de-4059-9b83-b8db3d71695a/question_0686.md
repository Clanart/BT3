# Q686: NEAR claim_fee entry recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public `claim_fee` proof-submission flow` that makes `near/omni-bridge/src/lib.rs::claim_fee` settle principal to one party but authorize fee claim or callback routing for another due to verifies a `FinTransfer` proof and forwards the predecessor account into `claim_fee_callback` for fee release, violating `fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee`
- Entrypoint: `public `claim_fee` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and caller identity as purported fee recipient
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
