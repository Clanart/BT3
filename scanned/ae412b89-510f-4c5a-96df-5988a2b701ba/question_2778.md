# Q2778: NEAR Wormhole FinTransfer conversion emitter or factory binding mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole fee-claim proof flow` and then replay or reorder later fee-claim proof submission so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` ends up accepting two inconsistent interpretations of the same economic event specifically around `emitter or factory binding mismatch` under Borsh-decodes a Wormhole payload into `FinTransferMessage` and derives the emitter address from the token chain and VAA emitter bytes, violating `fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>`
- Entrypoint: `public Wormhole fee-claim proof flow`
- Attacker controls: payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
