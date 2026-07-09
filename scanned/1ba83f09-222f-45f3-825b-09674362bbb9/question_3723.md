# Q3723: NEAR fin_transfer callback replay state keyed too narrowly for the true domain

## Question
Can an unprivileged attacker exploit `proof callback reached from public `fin_transfer`` so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` treats two events from different chains, assets, or message classes as sharing one replay slot because of decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly.
