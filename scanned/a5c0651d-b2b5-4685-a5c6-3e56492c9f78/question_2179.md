# Q2179: NEAR Wormhole LogMetadata conversion optional-field encoding ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole metadata proof flow` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional-field encoding ambiguity` under Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
