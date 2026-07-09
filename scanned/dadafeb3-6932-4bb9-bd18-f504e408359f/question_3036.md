# Q3036: NEAR fin_transfer callback storage-preparation omission changes settlement meaning at boundary values

## Question
Can an unprivileged attacker trigger `proof callback reached from public `fin_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fin_transfer_callback` violate `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement` in the `storage-preparation omission changes settlement meaning` attack class because decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
