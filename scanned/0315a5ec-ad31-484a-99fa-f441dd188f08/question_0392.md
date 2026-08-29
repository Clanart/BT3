# Q0392: principal-ratio-reduction via transfer: judge a position against an LTV belonging to a different a

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `principal-ratio-reduction` (mainnet/contracts/vault/v0-vault-stx.clar:406) in a state where it judge a position against an LTV belonging to a different asset set? Given that it derives a principal reduction from an amount, the scaled principal and the previewed debt, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:406` -> `principal-ratio-reduction`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `principal-ratio-reduction` derives a principal reduction from an amount, the scaled principal and the previewed debt. Reach it through `transfer` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with `amount` varied, and assert that the value `principal-ratio-reduction` returns is identical in both runs; a divergence confirms the finding.
