# Q1344: Race upsert_move_to_vault_txid_from_citrea_deposit across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the `old_move_txid` in `ReplacementDeposit` so `upsert_move_to_vault_txid_from_citrea_deposit` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::upsert_move_to_vault_txid_from_citrea_deposit
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: use retries, batching, or timing around the `old_move_txid` in `ReplacementDeposit` to desynchronize state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
