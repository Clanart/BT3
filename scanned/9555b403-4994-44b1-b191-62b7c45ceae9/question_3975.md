# Q3975: NEAR fin_transfer callback replay state keyed too narrowly for the true domain through cross-module drift

## Question
Can an unprivileged attacker use `proof callback reached from public `fin_transfer`` with control over decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay state keyed too narrowly for the true domain` attack class because decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
