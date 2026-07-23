# Q3745: Break reimbursement recoverability in save_state_machines

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `deposit_outpoint` and its on-chain prevout details so `save_state_machines` moves the protocol past the point where reimbursement should remain recoverable, leaving the verifier nonce session that a final signature is supposed to consume inconsistent with the assumption that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/state_machine.rs::save_state_machines
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
