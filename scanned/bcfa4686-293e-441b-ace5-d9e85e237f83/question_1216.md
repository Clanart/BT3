# Q1216: NEAR EVM trie proof verifier parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reachable from public EVM proof verification` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` violate `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root` in the `parser boundary or offset manipulation` attack class because walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
