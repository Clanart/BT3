# Q0187: calc-index-next via liquidate-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
`calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) applies a multiplier to the current index. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the vault whose share price the redemption moves, use that to satisfy the freshness gate with a timestamp the gate was never meant to accept, violating the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `liquidate-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the vault whose share price the redemption moves, then read `calc-index-next` state before and after in the same block and assert the two sides of the invariant are equal.
