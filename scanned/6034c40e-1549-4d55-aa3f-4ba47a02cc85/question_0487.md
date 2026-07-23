# Q487: Misbind trusted context inside internal_get_emergency_stop_tx

## Question
Can an unprivileged attacker reach `internal_get_emergency_stop_tx` through public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request and make attacker-controlled the `old_move_txid` in `ReplacementDeposit` bind to the wrong trusted context, so the nofn aggregate key and covenant context is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_get_emergency_stop_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: bind attacker-controlled the `old_move_txid` in `ReplacementDeposit` to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
