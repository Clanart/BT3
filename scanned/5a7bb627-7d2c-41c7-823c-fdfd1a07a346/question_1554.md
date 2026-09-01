# Q1554: `from_txs` and domain separation between hashed structures

## Question
Can a prover exploit `from_txs` in `circuits-lib/src/bridge_circuit/structs.rs` hashing two different structures into the same digest space (mid-state txid vs internal node, journal fields concatenated without length prefixes, `DepositConstant` fields packed ambiguously) to produce two distinct protocol facts with one digest, so a proof for one is accepted for the other?

## Target
- File/function: `circuits-lib/src/bridge_circuit/structs.rs` -> `from_txs` (This module defines the data structures used in the Bridge Circuit)
- Entrypoint: a Groth16 proof with colliding preimages -> `from_txs`
- Attacker controls: the byte layout of every structure the attacker submits to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: collide two protocol facts under one committed digest
- Invariant to test: the digest `from_txs` computes is injective over the protocol facts it is meant to distinguish
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct two distinct inputs and assert `from_txs` yields different digests
