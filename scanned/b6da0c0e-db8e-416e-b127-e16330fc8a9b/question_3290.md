# Q3290: Root Selection Confusion With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `try_from` verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root so `the root consumed by the handler` becomes inconsistent with `the root that actually stores the authenticated message or receipt`, breaking the invariant that each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/pallets/ismp/src/events.rs::try_from
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Mutate only the root family expected by the path and assert the handler rejects before any callback, receipt write, or timeout side effect occurs. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
