# Q3849: NEAR fin_transfer callback replay state keyed too narrowly for the true domain via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `fin_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `replay state keyed too narrowly for the true domain` under decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
