# Q1922: debt-preview via accrue: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) while controlling whether an earlier call in the same block already advanced last-update, can an unprivileged attacker make `debt-preview` (mainnet/contracts/vault/v0-vault-stx.clar:331) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `debt-preview` computes cumulative debt from `principal-scaled` and the FORWARD index, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:331` -> `debt-preview`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: whether an earlier call in the same block already advanced last-update
- Exploit idea: `debt-preview` computes cumulative debt from `principal-scaled` and the FORWARD index. Reach it through `accrue` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `accrue` twice with whether an earlier call in the same block already advanced last-update varied, and assert that the value `debt-preview` returns is identical in both runs; a divergence confirms the finding.
