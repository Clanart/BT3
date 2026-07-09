# Q2443: NEAR fin_transfer callback delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `proof callback reached from public `fin_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fin_transfer_callback` violate `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement` in the `delivery callback leaves inconsistent state` attack class because decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
