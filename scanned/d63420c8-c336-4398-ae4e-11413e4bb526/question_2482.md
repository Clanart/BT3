# Q2482: NEAR Wormhole DeployToken conversion optional-field encoding ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole deploy-token proof flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` violate `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model` in the `optional-field encoding ambiguity` attack class because Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
