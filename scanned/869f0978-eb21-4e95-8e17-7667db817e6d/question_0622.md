# Q622: Misbind trusted context inside get_assert_scripts

## Question
Can an unprivileged attacker reach `get_assert_scripts` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the streamed nonce-session identifiers and public nonce ordering bind to the wrong trusted context, so the nofn aggregate key and covenant context is interpreted for one bridge action while authorizing another, violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/bitvm_client.rs::get_assert_scripts
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: bind attacker-controlled the streamed nonce-session identifiers and public nonce ordering to the wrong trusted bridge context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
