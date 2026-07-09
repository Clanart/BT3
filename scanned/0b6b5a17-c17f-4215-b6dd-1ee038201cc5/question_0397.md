# Q397: NEAR token-deployer deploy_token canonical token identity collision through cross-module drift

## Question
Can an unprivileged attacker use `cross-contract token deployment reached from public Near `deploy_token` callback` with control over account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state and desynchronize `near/token-deployer/src/lib.rs::deploy_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `canonical token identity collision` attack class because controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Also assert cross-module consistency between `near/token-deployer/src/lib.rs::deploy_token` and the adjacent token-mapping and asset-identity logic after every branch.
