# Q2025: `get_merkle_root_of_txin` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `get_merkle_root_of_txin` in `core/src/builder/transaction/txhandler.rs` builds (round index, kickoff index, watchtower ordering, `PartialInputs` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/builder/transaction/txhandler.rs` -> `get_merkle_root_of_txin` (This module defines the [`TxHandler`] abstraction, which wraps a protocol transaction and its metadata)
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `get_merkle_root_of_txin`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
