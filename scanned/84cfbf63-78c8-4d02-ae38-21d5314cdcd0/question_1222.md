# Q1222: NEAR Wormhole DeployToken conversion parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole deploy-token proof flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` violate `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model` in the `parser boundary or offset manipulation` attack class because Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
