# Q3007: Decouple emergency protection in create_disprove_txhandler

## Question
Can an unprivileged attacker push attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_disprove_txhandler` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the nofn aggregate key and covenant context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/builder/transaction/challenge.rs::create_disprove_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
