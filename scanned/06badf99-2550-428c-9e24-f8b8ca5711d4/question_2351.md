# Q2351: Confuse replacement linkage in on_initial_collateral_entry

## Question
Can an unprivileged attacker shape the streamed nonce-session identifiers and public nonce ordering so `on_initial_collateral_entry` confuses replacement and non-replacement contexts, causing the nofn aggregate key and covenant context to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/round.rs::on_initial_collateral_entry
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
