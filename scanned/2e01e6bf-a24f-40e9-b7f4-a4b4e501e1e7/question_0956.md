# Q0956: refresh via borrow: make a required price path abort so the position can no lo

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it make a required price path abort so the position can no longer be closed or seized? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `borrow` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with `receiver`, including a contract principal varied, and assert that the value `refresh` returns is identical in both runs; a divergence confirms the finding.
