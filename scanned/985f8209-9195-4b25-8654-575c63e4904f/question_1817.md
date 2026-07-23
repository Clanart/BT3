# Q1817: Leave reusable partial state in update_replacement_deposit_move_txid

## Question
Can an unprivileged attacker force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `update_replacement_deposit_move_txid` continues from stale intermediate state, causing the operator signature set attached to a deposit to diverge from the canonical bridge context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::update_replacement_deposit_move_txid
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume under changed state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
