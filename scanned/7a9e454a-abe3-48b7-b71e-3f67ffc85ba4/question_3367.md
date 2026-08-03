# Q3367: Root Selection Confusion Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `execute` verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root so `the root consumed by the handler` becomes inconsistent with `the root that actually stores the authenticated message or receipt`, breaking the invariant that each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/pallets/ismp/src/impls.rs::execute
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Verify a request, response, or timeout against the wrong state root, overlay root, or child-trie root. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: each handler must verify membership or non-membership against the exact trie that stores the relevant message or receipt data
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Mutate only the root family expected by the path and assert the handler rejects before any callback, receipt write, or timeout side effect occurs. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
