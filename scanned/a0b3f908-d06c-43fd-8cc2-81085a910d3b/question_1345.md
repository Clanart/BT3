# Q1345: Race update_replacement_deposit_move_txid across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the set of verifier, operator, or watchtower keys that get associated with the deposit context so `update_replacement_deposit_move_txid` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::update_replacement_deposit_move_txid
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: use retries, batching, or timing around the set of verifier, operator, or watchtower keys that get associated with the deposit context to desynchronize state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
