# Q5603: `generate_bitvm_pks_for_deposit` and the presigned transaction graph's identity

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator choose deposit-time parameters that change what `generate_bitvm_pks_for_deposit` in `core/src/actor.rs` builds (round index, kickoff index, watchtower ordering, `WinternitzDerivationPath` fields) so that one presigned signature is valid for two structurally different transactions in the graph, letting a bridge UTXO be spent by a transaction the verifiers never approved?

## Target
- File/function: `core/src/actor.rs` -> `generate_bitvm_pks_for_deposit`
- Entrypoint: aggregator `NewDeposit` -> `create_txhandlers` -> `generate_bitvm_pks_for_deposit`
- Attacker controls: every deposit-time index and ordering that feeds transaction construction; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: collide two graph transactions under one signature
- Invariant to test: each presigned signature verifies for exactly one transaction of the deposit's graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: enumerate the graph for one deposit and assert all sighashes are pairwise distinct
