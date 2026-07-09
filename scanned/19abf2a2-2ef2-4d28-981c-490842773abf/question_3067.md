# Q3067: NEAR EVM trie proof verifier address normalization changes authenticated subject at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reachable from public EVM proof verification` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` violate `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root` in the `address normalization changes authenticated subject` attack class because walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
