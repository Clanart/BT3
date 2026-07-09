# Q2629: NEAR Wormhole ParsedVAA::parse signature malleability or alternate recovery

## Question
Can an unprivileged attacker submit alternate signature encodings through `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` treats as authorizing the same or a different bridge action because of parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload, violating `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Target `v/r/s` normalization, ECDSA recovery semantics, and Ethereum-style signature handling on non-Ethereum chains.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Try low-s/high-s and alternate-`v` forms and assert that recovery either rejects them or yields one unique signer and one unique message.
