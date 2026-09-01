# Q0506: `get_idx_path` and domain separation between hashed structures

## Question
Can a prover exploit `get_idx_path` in `circuits-lib/src/bridge_circuit/merkle_tree.rs` hashing two different structures into the same digest space (mid-state txid vs internal node, journal fields concatenated without length prefixes, `BlockInclusionProof` fields packed ambiguously) to produce two distinct protocol facts with one digest, so a proof for one is accepted for the other?

## Target
- File/function: `circuits-lib/src/bridge_circuit/merkle_tree.rs` -> `get_idx_path` (This module implements a Bitcoin Merkle tree structure, which is used to verify the integrity of transactions in a block)
- Entrypoint: a Groth16 proof with colliding preimages -> `get_idx_path`
- Attacker controls: the byte layout of every structure the attacker submits to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: collide two protocol facts under one committed digest
- Invariant to test: the digest `get_idx_path` computes is injective over the protocol facts it is meant to distinguish
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct two distinct inputs and assert `get_idx_path` yields different digests
