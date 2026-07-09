# Q2595: NEAR fin_transfer callback storage-preparation omission changes settlement meaning

## Question
Can an unprivileged attacker make `proof callback reached from public `fin_transfer`` omit or reorder required storage setup so that `near/omni-bridge/src/lib.rs::fin_transfer_callback` settles under a different assumption about who can receive principal or fees because of decodes `ProverResult::InitTransfer`, checks the factory mapping, denormalizes amount and fee, allocates a new destination nonce, and routes to Near or non-Near settlement, violating `the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback`
- Entrypoint: `proof callback reached from public `fin_transfer``
- Attacker controls: decoded prover result, origin chain, token mapping, decimals, storage-deposit action order, and recipient chain
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting.
- Invariant to test: the validated source transfer, denormalized value, and chosen destination branch must remain bound to the same origin event throughout settlement
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned.
