# Q2041: Confuse replacement linkage in get_assert_taproot_leaf_hashes

## Question
Can an unprivileged attacker shape the `recovery_taproot_address` in `BaseDeposit` so `get_assert_taproot_leaf_hashes` confuses replacement and non-replacement contexts, causing the emergency-stop transaction that should protect the same deposit to inherit the wrong history and violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/bitvm_client.rs::get_assert_taproot_leaf_hashes
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
