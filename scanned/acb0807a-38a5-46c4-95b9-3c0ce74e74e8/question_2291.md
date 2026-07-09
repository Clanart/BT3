# Q2291: NEAR fin_transfer callback delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `proof callback reached from public `fin_transfer`` with control over decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
