# Q343: NEAR fin_transfer callback replay guard can be bypassed or consumed incorrectly through cross-module drift

## Question
Can an unprivileged attacker use `proof callback reached from public `fin_transfer`` with control over decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay guard can be bypassed or consumed incorrectly` attack class because decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
