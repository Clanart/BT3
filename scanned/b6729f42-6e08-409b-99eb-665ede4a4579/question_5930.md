# Q5930: relevant via liquidate-multi: make a required price path abort so the position can no lo

## Question
Entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) while controlling the trait principals supplied per entry, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) make a required price path abort so the position can no longer be closed or seized? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `liquidate-multi` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the trait principals supplied per entry varied, and assert that the value `relevant` returns is identical in both runs; a divergence confirms the finding.
