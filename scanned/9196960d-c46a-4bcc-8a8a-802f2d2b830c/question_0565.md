# Q565: NEAR token-deployer deploy_token canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `cross-contract token deployment reached from public Near `deploy_token` callback` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/token-deployer/src/lib.rs::deploy_token` violate `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model` in the `canonical token identity collision` attack class because controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical becomes fragile at those edges?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
