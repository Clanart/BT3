# Q3776: unpack-u16 via liquidate-multi: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls which borrowers are placed early versus late in the batch reach `unpack-u16` (mainnet/contracts/vault/v0-vault-stx.clar:259) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it unpacks eight u16 curve fields from one packed word, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:259` -> `unpack-u16`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `unpack-u16` unpacks eight u16 curve fields from one packed word. Reach it through `liquidate-multi` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with which borrowers are placed early versus late in the batch varied, and assert that the value `unpack-u16` returns is identical in both runs; a divergence confirms the finding.
