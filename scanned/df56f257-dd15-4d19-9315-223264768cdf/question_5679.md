# Q5679: `from_secp_kp` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `from_secp_kp` in `core/src/musig2.rs` builds (round index, kickoff index, watchtower ordering, `Musig2Mode` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/musig2.rs` -> `from_secp_kp` (Helper functions for the MuSig2 signature scheme)
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `from_secp_kp`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
