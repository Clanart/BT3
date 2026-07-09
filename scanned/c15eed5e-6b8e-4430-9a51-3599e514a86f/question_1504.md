# Q1504: NEAR fin_transfer callback asset-branch confusion on finalization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `fin_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `asset-branch confusion on finalization` under decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
