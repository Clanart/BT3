# Q175: NEAR fin_transfer callback replay guard can be bypassed or consumed incorrectly via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `fin_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `replay guard can be bypassed or consumed incorrectly` under decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
