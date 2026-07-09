# Q3045: NEAR deploy_token entry same remote asset deployable via multiple proof paths at boundary values

## Question
Can an unprivileged attacker trigger `public `deploy_token` proof-submission flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token` violate `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting` in the `same remote asset deployable via multiple proof paths` attack class because verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
