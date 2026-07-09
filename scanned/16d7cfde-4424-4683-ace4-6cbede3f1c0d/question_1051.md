# Q1051: NEAR Wormhole prover verify_proof parser boundary or offset manipulation through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole proof verifier entrypoint` with control over serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `parser boundary or offset manipulation` attack class because logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
