# Q1704: NEAR Wormhole ParsedVAA::parse parser boundary or offset manipulation through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` with control over raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `parser boundary or offset manipulation` attack class because parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload, violating `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` and the adjacent proof parsing and source authentication after every branch.
