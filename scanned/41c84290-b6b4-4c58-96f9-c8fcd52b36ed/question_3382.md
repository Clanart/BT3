# Q3382: `get_signature_id` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `get_signature_id` in `core/src/builder/transaction/input.rs` builds (round index, kickoff index, watchtower ordering, `SpentTxIn` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/builder/transaction/input.rs` -> `get_signature_id` (This module defines types and utilities for representing and handling transaction inputs used in [`super::TxHandler`])
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `get_signature_id`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
