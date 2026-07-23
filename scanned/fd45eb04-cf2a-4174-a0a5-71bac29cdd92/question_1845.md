# Q1845: Leave reusable partial state in upsert_full_block

## Question
Can an unprivileged attacker force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `upsert_full_block` continues from stale intermediate state, causing the verifier nonce session that a final signature is supposed to consume to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::upsert_full_block
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: force a partial failure around the aggregate nonce / partial-signature sequencing across repeated requests and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
