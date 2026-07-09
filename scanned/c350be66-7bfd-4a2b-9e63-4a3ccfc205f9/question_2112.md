# Q2112: NEAR deploy_token_internal malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with a malicious token or metadata payload so that `near/omni-bridge/src/lib.rs::deploy_token_internal` records a deceptive asset identity that later drives deployment or claims, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
