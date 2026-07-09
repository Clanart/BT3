# Q3070: NEAR Wormhole ParsedVAA::parse signature malleability or alternate recovery at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` violate `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body` in the `signature malleability or alternate recovery` attack class because parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Target `v/r/s` normalization, ECDSA recovery semantics, and Ethereum-style signature handling on non-Ethereum chains. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Try low-s/high-s and alternate-`v` forms and assert that recovery either rejects them or yields one unique signer and one unique message. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
