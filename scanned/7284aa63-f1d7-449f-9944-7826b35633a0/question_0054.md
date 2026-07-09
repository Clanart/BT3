# Q54: NEAR Wormhole LogMetadata conversion proof kind or event class confusion

## Question
Can an unprivileged attacker submit bytes through `public Wormhole metadata proof flow` that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` validates as one proof or event class but later interprets as another because of Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action.
