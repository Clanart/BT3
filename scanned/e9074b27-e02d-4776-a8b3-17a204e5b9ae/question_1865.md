# Q1865: NEAR Wormhole ParsedVAA::parse parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` violate `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body` in the `parser boundary or offset manipulation` attack class because parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
