# Q2540: receive-underlying via accrue: judge a position against an LTV belonging to a different a

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls the block time at which accrual is first triggered in a block reach `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) in a state where it judge a position against an LTV belonging to a different asset set? Given that it pulls the underlying from a named account, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: the block time at which accrual is first triggered in a block
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `accrue` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `accrue` twice with the block time at which accrual is first triggered in a block varied, and assert that the value `receive-underlying` returns is identical in both runs; a divergence confirms the finding.
