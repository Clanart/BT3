# Q2027: NEAR Wormhole LogMetadata conversion optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public Wormhole metadata proof flow` so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` authenticates one payload but downstream logic interprets another because of Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
