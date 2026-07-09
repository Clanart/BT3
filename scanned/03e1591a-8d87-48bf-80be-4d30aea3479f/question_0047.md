# Q47: NEAR EVM trie proof verifier proof kind or event class confusion

## Question
Can an unprivileged attacker submit bytes through `internal helper reachable from public EVM proof verification` that `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` validates as one proof or event class but later interprets as another because of walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action.
