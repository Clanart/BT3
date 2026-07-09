# Q16: NEAR deploy_token entry canonical token identity collision

## Question
Can an unprivileged attacker reach `public `deploy_token` proof-submission flow` with a valid-looking remote asset identity and make `near/omni-bridge/src/lib.rs::deploy_token` map it onto an existing local token because of verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near, violating `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row.
