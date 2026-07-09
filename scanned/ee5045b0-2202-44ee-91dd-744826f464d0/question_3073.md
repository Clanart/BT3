# Q3073: NEAR Wormhole DeployToken conversion address normalization changes authenticated subject at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole deploy-token proof flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` violate `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model` in the `address normalization changes authenticated subject` attack class because Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
