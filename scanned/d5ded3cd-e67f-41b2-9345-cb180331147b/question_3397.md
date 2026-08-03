# Q3397: Duplicate Batch Execution Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `handle_unsigned` execute the same request, response, or timeout more than once inside one batch or across two batches so `the one-time execution marker` becomes inconsistent with `the unique authenticated message set`, breaking the invariant that duplicate messages must be rejected before any external callback can produce a second execution or second settlement and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/pallets/ismp/src/lib.rs::handle_unsigned
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Execute the same request, response, or timeout more than once inside one batch or across two batches. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: duplicate messages must be rejected before any external callback can produce a second execution or second settlement
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Create a batch with duplicates or replay the same batch and assert callbacks, receipts, and balance-moving side effects occur at most once. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
