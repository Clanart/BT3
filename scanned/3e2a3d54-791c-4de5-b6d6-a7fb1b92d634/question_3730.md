# Q3730: NEAR claim_fee entry fee recipient can be substituted or reclaimed by attacker

## Question
Can an unprivileged attacker use `public `claim_fee` proof-submission flow` to make `near/omni-bridge/src/lib.rs::claim_fee` route a legitimate fee to the wrong account because of verifies a `FinTransfer` proof and forwards the predecessor account into `claim_fee_callback` for fee release, violating `fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee`
- Entrypoint: `public `claim_fee` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and caller identity as purported fee recipient
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths.
- Invariant to test: fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee.
