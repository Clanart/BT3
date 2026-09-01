# Q3592: `calculate_sighash_txin` and the sighash stream the verifiers actually sign

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator shape a deposit so that the ordering or count of sighashes produced for `calculate_sighash_txin` in `core/src/builder/transaction/txhandler.rs` (`create_nofn_sighash_stream`, `TxHandlerBuilder`) differs between the aggregator and one verifier, so a partial signature intended for one transaction of the presigned graph is aggregated onto a different one - for example a payout or reimbursement instead of a timeout?

## Target
- File/function: `core/src/builder/transaction/txhandler.rs` -> `calculate_sighash_txin` (This module defines the [`TxHandler`] abstraction, which wraps a protocol transaction and its metadata)
- Entrypoint: aggregator `NewDeposit` signing rounds -> `calculate_sighash_txin`
- Attacker controls: the deposit's actor counts, operator count and round/kickoff indices that size the sighash stream; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: get an N-of-N signature bound to a transaction the verifiers never intended to authorise
- Invariant to test: the i-th sighash the aggregator distributes == the i-th sighash every verifier derives for the same `DepositData`
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert the sighash stream is byte-identical across independently constructed `DepositData` with permuted actor lists
