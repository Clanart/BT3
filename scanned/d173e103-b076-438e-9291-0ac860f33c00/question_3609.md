# Q3609: collateral-add via liquidate-multi: apply a transform after the gate that was supposed to boun

## Question
Can an unprivileged attacker entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), controlling the trait principals supplied per entry, drive `collateral-add` (mainnet/contracts/market/v0-market-vault.clar:374) — which evaluates the map write and `mask-update` as `let` bindings BEFORE `check-impl-auth`, the pause state and the amount assertion — to apply a transform after the gate that was supposed to bound its output, breaking the invariant that collateral is valued low and debt is valued high at every call site without exception, and cause temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:374` -> `collateral-add`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `collateral-add` evaluates the map write and `mask-update` as `let` bindings BEFORE `check-impl-auth`, the pause state and the amount assertion. Reach it through `liquidate-multi` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `collateral-add` touches, run `liquidate-multi` with the trait principals supplied per entry, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
