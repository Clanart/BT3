# Q1217: NEAR Wormhole prover verify_proof parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole proof verifier entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` violate `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation` in the `parser boundary or offset manipulation` attack class because logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
