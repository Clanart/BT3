# Q2927: NEAR Wormhole LogMetadata conversion address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole metadata proof flow` with control over payload bytes inside a validated VAA, token address chain, name, symbol, and decimals and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because Borsh-decodes a Wormhole payload into `LogMetadataMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` and the adjacent token-mapping and asset-identity logic after every branch.
