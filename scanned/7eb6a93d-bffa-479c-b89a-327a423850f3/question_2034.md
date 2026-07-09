# Q2034: NEAR token-deployer deploy_token native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `cross-contract token deployment reached from public Near `deploy_token` callback` and make `near/token-deployer/src/lib.rs::deploy_token` treat a wrapped asset as native or a native asset as wrapped because of controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
