# Q2407: Root Selection Confusion After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and replaying the same public flow after one part of storage changed and another part did not, and make `handle_incoming_message` verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root so `the root consumed by the handler` becomes inconsistent with `the root that actually stores the authenticated message or receipt`, breaking the invariant that each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/ismp/core/src/handlers.rs::handle_incoming_message
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Mutate only the root family expected by the path and assert the handler rejects before any callback, receipt write, or timeout side effect occurs. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
