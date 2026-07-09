# Q3889: NEAR token-deployer deploy_token mint-with-message path differs economically from plain mint via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `cross-contract token deployment reached from public Near `deploy_token` callback` and then replay or reorder later callback or refund resolution so that `near/token-deployer/src/lib.rs::deploy_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `mint-with-message path differs economically from plain mint` under controller-only deployer creates a new token subaccount and initializes metadata that downstream bridge flows will trust as canonical, violating `deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model`?

## Target
- File/function: `near/token-deployer/src/lib.rs::deploy_token`
- Entrypoint: `cross-contract token deployment reached from public Near `deploy_token` callback`
- Attacker controls: account id chosen for the new token, metadata supplied by a validated bridge proof, and global code hash state
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: deployment into a predictable subaccount must not let an attacker reuse state, collide names, or create a token whose runtime semantics diverge from the bridge’s assumed wrapped-token model
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
