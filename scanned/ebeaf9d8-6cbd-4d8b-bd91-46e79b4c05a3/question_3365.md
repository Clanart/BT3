# Q3365: Break reimbursement recoverability in find_and_mark_unspent_kickoff_utxos

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the aggregate nonce / partial-signature sequencing across repeated requests so `find_and_mark_unspent_kickoff_utxos` moves the protocol past the point where reimbursement should remain recoverable, leaving the deposit-to-move-tx binding inconsistent with the assumption that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/operator.rs::find_and_mark_unspent_kickoff_utxos
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
