# Q1409: `get_num_required_operator_sigs_per_kickoff` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `get_num_required_operator_sigs_per_kickoff` in `core/src/builder/sighash.rs` builds (round index, kickoff index, watchtower ordering, `PartialSignatureInfo` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/builder/sighash.rs` -> `get_num_required_operator_sigs_per_kickoff` (This module provides functions and types for constructing signature hashes (sighashes) for the transactions in the Clementine bridge protocol)
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `get_num_required_operator_sigs_per_kickoff`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
