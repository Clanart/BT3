# Q860: Misbind trusted context inside check_if_utxo_spending_tx_is_finalized

## Question
Can an unprivileged attacker reach `check_if_utxo_spending_tx_is_finalized` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests bind to the wrong trusted context, so the operator signature set attached to a deposit is interpreted for one bridge action while authorizing another, violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::check_if_utxo_spending_tx_is_finalized
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: bind attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests to the wrong trusted bridge context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
