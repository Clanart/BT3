# Q3625: NEAR Wormhole LogMetadata conversion canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole metadata proof flow` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` violate `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token` in the `canonical token identity collision` attack class because Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
