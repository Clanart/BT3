# Q1608: NEAR foreign/native token mapping lookup native versus wrapped registration confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public multi-hop settlement flows that map tokens across chains` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-bridge/src/lib.rs::get_bridged_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped registration confusion` under resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
