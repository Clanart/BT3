# Q1554: NEAR token-deployer deploy_token malicious metadata manufactures a bridge identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `cross-contract token deployment reached from public Near `deploy_token` callback` and then replay or reorder later callback or refund resolution so that `near/token-deployer/src/lib.rs::deploy_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `malicious metadata manufactures a bridge identity` under controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
