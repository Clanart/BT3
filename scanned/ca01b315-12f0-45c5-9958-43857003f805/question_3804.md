# Q3804: NEAR foreign/native token mapping lookup upgraded token changes supply semantics under live bridge mappings

## Question
Can an unprivileged attacker leverage public bridge flows after `public multi-hop settlement flows that map tokens across chains` so that `near/omni-bridge/src/lib.rs::get_bridged_token` interacts with an upgraded token whose mint/burn semantics no longer match prior assumptions, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks.
