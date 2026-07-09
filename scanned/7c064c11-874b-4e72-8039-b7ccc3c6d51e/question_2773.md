# Q2773: NEAR EVM trie proof verifier address normalization changes authenticated subject via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reachable from public EVM proof verification` and then replay or reorder another proof-consuming public entrypoint so that `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` ends up accepting two inconsistent interpretations of the same economic event specifically around `address normalization changes authenticated subject` under walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
