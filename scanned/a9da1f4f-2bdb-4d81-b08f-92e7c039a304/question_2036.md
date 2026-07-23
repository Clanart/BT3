# Q2036: Confuse replacement linkage in create_dummy_checksig_script

## Question
Can an unprivileged attacker shape the aggregate nonce / partial-signature sequencing across repeated requests so `create_dummy_checksig_script` confuses replacement and non-replacement contexts, causing the verifier nonce session that a final signature is supposed to consume to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/actor.rs::create_dummy_checksig_script
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
