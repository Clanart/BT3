# Q2690: calc-cumulative-debt via redeem: normalize a real holding to zero USD while the paired debt

## Question
Entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) while controlling `min-out`, can an unprivileged attacker make `calc-cumulative-debt` (mainnet/contracts/vault/v0-vault-stx.clar:180) normalize a real holding to zero USD while the paired debt normalizes upward? `calc-cumulative-debt` multiplies scaled principal by an index, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:180` -> `calc-cumulative-debt`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `calc-cumulative-debt` multiplies scaled principal by an index. Reach it through `redeem` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `min-out` varied, and assert that the value `calc-cumulative-debt` returns is identical in both runs; a divergence confirms the finding.
