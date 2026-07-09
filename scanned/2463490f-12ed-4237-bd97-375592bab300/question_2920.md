# Q2920: NEAR EVM trie proof verifier address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reachable from public EVM proof verification` with control over receipt index key, trie nodes, node ordering, and branch/leaf path structure and desynchronize `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` and the adjacent proof parsing and source authentication after every branch.
