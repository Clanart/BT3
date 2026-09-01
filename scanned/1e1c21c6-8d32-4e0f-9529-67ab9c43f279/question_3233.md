# Q3233: `calculate_double_sha256` and domain separation between hashed structures

## Question
Can a prover exploit `calculate_double_sha256` in `circuits-lib/src/common/hashes.rs` hashing two different structures into the same digest space (mid-state txid vs internal node, journal fields concatenated without length prefixes, `the module's input struct` fields packed ambiguously) to produce two distinct protocol facts with one digest, so a proof for one is accepted for the other?

## Target
- File/function: `circuits-lib/src/common/hashes.rs` -> `calculate_double_sha256` (Common hashing functions used in the Clementine protocol)
- Entrypoint: a Groth16 proof with colliding preimages -> `calculate_double_sha256`
- Attacker controls: the byte layout of every structure the attacker submits to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: collide two protocol facts under one committed digest
- Invariant to test: the digest `calculate_double_sha256` computes is injective over the protocol facts it is meant to distinguish
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct two distinct inputs and assert `calculate_double_sha256` yields different digests
