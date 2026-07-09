# Q1186: NEAR deploy_token entry partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `public `deploy_token` proof-submission flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token` violate `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting` in the `partial deployment rollback leaves live alias` attack class because verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
