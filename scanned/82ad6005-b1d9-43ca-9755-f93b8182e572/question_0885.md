# Q885: NEAR Wormhole prover verify_proof parser boundary or offset manipulation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole proof verifier entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` ends up accepting two inconsistent interpretations of the same economic event specifically around `parser boundary or offset manipulation` under logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
