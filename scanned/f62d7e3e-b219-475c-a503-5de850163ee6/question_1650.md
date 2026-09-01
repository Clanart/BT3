# Q1650: `calculate_sha256` and unbounded merkle path shape

## Question
Can a prover choose a merkle/MMR proof for `calculate_sha256` in `circuits-lib/src/common/hashes.rs` whose path length, leaf index or subroot selection is not tied to the actual block or chain size - reinterpreting an internal node as a leaf, or proving against a subroot the header chain never committed - so a transaction that was never mined is accepted as included?

## Target
- File/function: `circuits-lib/src/common/hashes.rs` -> `calculate_sha256` (Common hashing functions used in the Clementine protocol)
- Entrypoint: a forged inclusion proof submitted to the circuit -> `calculate_sha256`
- Attacker controls: the sibling list, path length, index bits and claimed root; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: forge an inclusion proof for a transaction that is not in the chain
- Invariant to test: the leaf proved included == a transaction in a block whose hash the header chain committed, at a path length equal to that block's tree depth
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct a shortened/extended path and assert `calculate_sha256` rejects it
