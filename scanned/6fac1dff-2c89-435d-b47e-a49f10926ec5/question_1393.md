# Q1393: NEAR token-deployer deploy_token malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `cross-contract token deployment reached from public Near `deploy_token` callback` with a malicious token or metadata payload so that `near/token-deployer/src/lib.rs::deploy_token` records a deceptive asset identity that later drives deployment or claims, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
