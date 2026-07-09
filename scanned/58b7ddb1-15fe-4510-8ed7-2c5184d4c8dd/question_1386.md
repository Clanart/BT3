# Q1386: NEAR Wormhole LogMetadata conversion emitter or factory binding mismatch

## Question
Can an unprivileged attacker submit a structurally valid proof to `public Wormhole metadata proof flow` whose payload points to one source chain while `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` authenticates another because of Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees.
