# Q5495: `create_move_to_vault_txhandler` and the sighash stream the verifiers actually sign

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator shape a deposit so that the ordering or count of sighashes produced for `create_move_to_vault_txhandler` in `core/src/builder/transaction/mod.rs` (`create_nofn_sighash_stream`, `the module's input struct`) differs between the aggregator and one verifier, so a partial signature intended for one transaction of the presigned graph is aggregated onto a different one - for example a payout or reimbursement instead of a timeout?

## Target
- File/function: `core/src/builder/transaction/mod.rs` -> `create_move_to_vault_txhandler` (This module provides the core logic for constructing, handling, and signing the various Bitcoin transactions)
- Entrypoint: aggregator `NewDeposit` signing rounds -> `create_move_to_vault_txhandler`
- Attacker controls: the deposit's actor counts, operator count and round/kickoff indices that size the sighash stream; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: get an N-of-N signature bound to a transaction the verifiers never intended to authorise
- Invariant to test: the i-th sighash the aggregator distributes == the i-th sighash every verifier derives for the same `DepositData`
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert the sighash stream is byte-identical across independently constructed `DepositData` with permuted actor lists
