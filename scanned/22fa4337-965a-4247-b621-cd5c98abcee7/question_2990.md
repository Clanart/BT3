# Q2990: Child-Trie-Root Misreward With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `beefy_consensus_proofs::submit_proof(origin, proof)` with attacker-controlled proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `offchain_key` pay for a proof as if it introduced new messages when it only replayed or re-proved an old child trie root so `the child-trie-root novelty check that gates reward` becomes inconsistent with `the actual novelty of the authenticated child trie root`, breaking the invariant that message-related proof rewards must depend on truly new authenticated message roots, not on alternate encodings of old roots and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/pallets/beefy-consensus-proofs/src/types.rs::offchain_key
- Entrypoint: beefy_consensus_proofs::submit_proof(origin, proof)
- Attacker controls: proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering
- Exploit idea: Pay for a proof as if it introduced new messages when it only replayed or re-proved an old child trie root. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: message-related proof rewards must depend on truly new authenticated message roots, not on alternate encodings of old roots
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Replay an old message root through a new proof encoding and assert message reward accounting does not pay again. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
