# Q3755: NEAR Wormhole ParsedVAA::parse address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` such that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` authenticates an address in one representation but later maps a normalized form to a different asset or account because of parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload, violating `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
