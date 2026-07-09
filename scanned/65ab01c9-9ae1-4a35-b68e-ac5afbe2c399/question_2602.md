# Q2602: NEAR claim_fee entry stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public `claim_fee` proof-submission flow` and make `near/omni-bridge/src/lib.rs::claim_fee` treat it as fresh because of verifies a `FinTransfer` proof and forwards the predecessor account into `claim_fee_callback` for fee release, violating `fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee`
- Entrypoint: `public `claim_fee` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and caller identity as purported fee recipient
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: fee claims must remain uniquely bound to one already-finalized destination event and one authentic fee recipient
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
