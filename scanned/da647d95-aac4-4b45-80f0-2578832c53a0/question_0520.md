# Q520: NEAR deploy_token entry canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public `deploy_token` proof-submission flow` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token` violate `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting` in the `canonical token identity collision` attack class because verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
