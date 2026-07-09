# Q3753: NEAR Wormhole prover verify_proof same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `public Wormhole proof verifier entrypoint` to deploy or bind the same remote asset through a second path because `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` authenticates logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback differently than another deploy path, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
