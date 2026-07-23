# Q2324: Confuse replacement linkage in insert_get_spent_utxos

## Question
Can an unprivileged attacker shape the deposit transaction timing, block placement, and confirmation ordering so `insert_get_spent_utxos` confuses replacement and non-replacement contexts, causing the verifier nonce session that a final signature is supposed to consume to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::insert_get_spent_utxos
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
