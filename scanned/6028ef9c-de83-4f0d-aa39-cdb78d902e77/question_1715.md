# Q1715: NEAR token-deployer deploy_token malicious metadata manufactures a bridge identity through cross-module drift

## Question
Can an unprivileged attacker use `cross-contract token deployment reached from public Near `deploy_token` callback` with control over account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state and desynchronize `near/token-deployer/src/lib.rs::deploy_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `malicious metadata manufactures a bridge identity` attack class because controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Also assert cross-module consistency between `near/token-deployer/src/lib.rs::deploy_token` and the adjacent token-mapping and asset-identity logic after every branch.
