# Q3484: NEAR Wormhole prover verify_proof address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole proof verifier entrypoint` with control over serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
