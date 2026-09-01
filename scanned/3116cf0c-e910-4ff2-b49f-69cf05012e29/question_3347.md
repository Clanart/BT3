# Q3347: `apply_block_headers` and domain separation between hashed structures

## Question
Can a prover exploit `apply_block_headers` in `circuits-lib/src/header_chain/mod.rs` hashing two different structures into the same digest space (mid-state txid vs internal node, journal fields concatenated without length prefixes, `HeaderChainCircuitInput` fields packed ambiguously) to produce two distinct protocol facts with one digest, so a proof for one is accepted for the other?

## Target
- File/function: `circuits-lib/src/header_chain/mod.rs` -> `apply_block_headers` (This module contains the implementation of the header chain circuit, which is basically)
- Entrypoint: a Groth16 proof with colliding preimages -> `apply_block_headers`
- Attacker controls: the byte layout of every structure the attacker submits to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: collide two protocol facts under one committed digest
- Invariant to test: the digest `apply_block_headers` computes is injective over the protocol facts it is meant to distinguish
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct two distinct inputs and assert `apply_block_headers` yields different digests
