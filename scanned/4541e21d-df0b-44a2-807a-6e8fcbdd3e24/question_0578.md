# Q0578: calc-utilization via call-ststx-ratio: make a required price path abort so the position can no lo

## Question
Entering through `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) while controlling whether the ratio is fetched before or after other state changes in the block, can an unprivileged attacker make `calc-utilization` (mainnet/contracts/vault/v0-vault-stx.clar:164) make a required price path abort so the position can no longer be closed or seized? `calc-utilization` divides debt by available liquidity, which can exceed BPS when debt outruns assets, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:164` -> `calc-utilization`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: whether the ratio is fetched before or after other state changes in the block
- Exploit idea: `calc-utilization` divides debt by available liquidity, which can exceed BPS when debt outruns assets. Reach it through `call-ststx-ratio` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `call-ststx-ratio` twice with whether the ratio is fetched before or after other state changes in the block varied, and assert that the value `calc-utilization` returns is identical in both runs; a divergence confirms the finding.
