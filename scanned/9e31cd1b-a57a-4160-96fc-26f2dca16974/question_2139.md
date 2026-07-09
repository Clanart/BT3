# Q2139: NEAR fin_transfer callback delivery callback leaves inconsistent state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `fin_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `delivery callback leaves inconsistent state` under decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
