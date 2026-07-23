# Q1379: Race add_and_get_txids_from_block across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the `old_move_txid` in `ReplacementDeposit` so `add_and_get_txids_from_block` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::add_and_get_txids_from_block
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: use retries, batching, or timing around the `old_move_txid` in `ReplacementDeposit` to desynchronize state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
