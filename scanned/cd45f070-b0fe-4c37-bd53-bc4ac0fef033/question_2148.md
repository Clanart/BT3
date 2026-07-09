# Q2148: NEAR deploy_token entry malicious metadata manufactures a bridge identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `deploy_token` proof-submission flow` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `malicious metadata manufactures a bridge identity` under verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near, violating `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
