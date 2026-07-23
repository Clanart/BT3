# Q3750: Break reimbursement recoverability in utxodb_json_encode_decode_invariant

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `deposit_outpoint` and its on-chain prevout details so `utxodb_json_encode_decode_invariant` moves the protocol past the point where reimbursement should remain recoverable, leaving the deposit-to-move-tx binding inconsistent with the assumption that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/wrapper.rs::utxodb_json_encode_decode_invariant
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
