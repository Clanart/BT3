# Q854: NEAR deploy_token entry partial deployment rollback leaves live alias via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `deploy_token` proof-submission flow` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `partial deployment rollback leaves live alias` under verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near, violating `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
