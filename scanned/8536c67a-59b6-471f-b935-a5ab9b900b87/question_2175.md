# Q2175: NEAR Wormhole ParsedVAA::parse emitter or factory binding mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole proof flow through `verify_proof -> verify_vaa_callback`` and then replay or reorder another proof-consuming public entrypoint so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse` ends up accepting two inconsistent interpretations of the same economic event specifically around `emitter or factory binding mismatch` under parses the VAA header/body offsets, hashes the body, and slices out timestamp, nonce, emitter chain/address, sequence, and payload, violating `byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::ParsedVAA::parse`
- Entrypoint: `public Wormhole proof flow through `verify_proof -> verify_vaa_callback``
- Attacker controls: raw VAA bytes, signer count, body offsets, emitter fields, payload length, and consistency level
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: byte slicing and offset arithmetic must never let malformed VAAs shift field boundaries or produce a locally-accepted payload different from the guardian-approved body
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
