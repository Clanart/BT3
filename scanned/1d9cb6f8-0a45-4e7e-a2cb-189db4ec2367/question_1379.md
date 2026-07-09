# Q1379: NEAR EVM trie proof verifier partial EVM validation leaves exploitable gap

## Question
Can an unprivileged attacker provide an EVM proof to `internal helper reachable from public EVM proof verification` that passes inclusion checks in `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof` while the decoded receipt or log still authorizes a different bridge action because of walks a Patricia trie over branch, extension, and leaf nodes using nibble-expanded receipt-index keys, violating `trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_trie_proof/_verify_trie_proof`
- Entrypoint: `internal helper reachable from public EVM proof verification`
- Attacker controls: receipt index key, trie nodes, node ordering, and branch/leaf path structure
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation.
- Invariant to test: trie traversal must reject every malformed branch/extension/leaf combination that could return attacker-chosen receipt bytes under the honest root
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds.
