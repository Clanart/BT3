# Q1701: NEAR EVM trie proof verifier partial EVM validation leaves exploitable gap through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reachable from public EVM proof verification` with control over receipt index key, trie nodes, node ordering, and branch/leaf path structure and desynchronize `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `partial EVM validation leaves exploitable gap` attack class because walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds. Also assert cross-module consistency between `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` and the adjacent proof parsing and source authentication after every branch.
