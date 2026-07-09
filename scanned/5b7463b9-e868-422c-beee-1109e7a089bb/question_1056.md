# Q1056: NEAR Wormhole DeployToken conversion parser boundary or offset manipulation through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole deploy-token proof flow` with control over payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `parser boundary or offset manipulation` attack class because Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` and the adjacent token-mapping and asset-identity logic after every branch.
