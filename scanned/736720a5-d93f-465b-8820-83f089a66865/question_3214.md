# Q3214: NEAR Wormhole prover verify_proof address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `public Wormhole proof verifier entrypoint` such that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` authenticates an address in one representation but later maps a normalized form to a different asset or account because of logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
