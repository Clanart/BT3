# Q3879: NEAR Wormhole prover verify_proof same remote asset deployable via multiple proof paths via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole proof verifier entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` ends up accepting two inconsistent interpretations of the same economic event specifically around `same remote asset deployable via multiple proof paths` under logs the supplied VAA, asks the external prover to validate it, and then parses the same VAA locally in the callback, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
