# Q1230: NEAR token-deployer deploy_token partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `cross-contract token deployment reached from public Near `deploy_token` callback` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/token-deployer/src/lib.rs::deploy_token` violate `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model` in the `partial deployment rollback leaves live alias` attack class because controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical becomes fragile at those edges?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
