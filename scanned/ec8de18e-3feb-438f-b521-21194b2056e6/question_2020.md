# Q2020: NEAR EVM trie proof verifier optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `internal helper reachable from public EVM proof verification` so that `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` authenticates one payload but downstream logic interprets another because of walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
