# Q3759: NEAR Wormhole LogMetadata conversion malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public Wormhole metadata proof flow` with a malicious token or metadata payload so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>` records a deceptive asset identity that later drives deployment or claims, violating `metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<LogMetadataMessage>`
- Entrypoint: `public Wormhole metadata proof flow`
- Attacker controls: payload bytes inside a validated VAA, token address chain, name, symbol, and decimals
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: metadata proofs must not be malleable across chain/address boundaries or string encodings that deploy the wrong wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
