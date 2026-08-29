# Q0768: mask-pos via collateral-add: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls call ordering within the block reach `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: call ordering within the block
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `collateral-add` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz call ordering within the block across its boundary values through `collateral-add` in simnet and assert `mask-pos` never returns a value that breaks the invariant.
