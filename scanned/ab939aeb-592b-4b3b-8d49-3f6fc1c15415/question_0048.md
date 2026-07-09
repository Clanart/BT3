# Q48: NEAR Wormhole prover verify_proof proof kind or event class confusion

## Question
Can an unprivileged attacker submit bytes through `public Wormhole proof verifier entrypoint` that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` validates as one proof or event class but later interprets as another because of logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action.
