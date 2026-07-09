# Q2086: NEAR foreign/native token mapping lookup fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public multi-hop settlement flows that map tokens across chains` to register or settle against a token that only looks bridge-controlled because `near/omni-bridge/src/lib.rs::get_bridged_token` relies on resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
