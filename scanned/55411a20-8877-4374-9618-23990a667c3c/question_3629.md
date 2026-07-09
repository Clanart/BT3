# Q3629: NEAR token-deployer deploy_token low-half deploy salt aliases another token id at boundary values

## Question
Can an unprivileged attacker trigger `cross-contract token deployment reached from public Near `deploy_token` callback` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/token-deployer/src/lib.rs::deploy_token` violate `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model` in the `low-half deploy salt aliases another token id` attack class because controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical becomes fragile at those edges?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
