# Q2288: Confuse replacement linkage in upsert_move_to_vault_txid_from_citrea_deposit

## Question
Can an unprivileged attacker shape the aggregate nonce / partial-signature sequencing across repeated requests so `upsert_move_to_vault_txid_from_citrea_deposit` confuses replacement and non-replacement contexts, causing the nofn aggregate key and covenant context to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/database/verifier.rs::upsert_move_to_vault_txid_from_citrea_deposit
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
