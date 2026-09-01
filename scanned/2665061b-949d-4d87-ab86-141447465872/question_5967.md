# Q5967: `get_deposit_sig_owner` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `get_deposit_sig_owner` in `core/src/builder/transaction/deposit_signature_owner.rs` builds (round index, kickoff index, watchtower ordering, `DepositSigKeyOwner` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/builder/transaction/deposit_signature_owner.rs` -> `get_deposit_sig_owner` (This module provides types and logic for mapping transaction signature requirements to protocol entities in the Clementine bridge)
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `get_deposit_sig_owner`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
