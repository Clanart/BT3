# Q1580: receive-underlying via redeem: apply a transform after the gate that was supposed to boun

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `amount` of shares burned reach `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it pulls the underlying from a named account, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `amount` of shares burned
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `redeem` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `amount` of shares burned varied, and assert that the value `receive-underlying` returns is identical in both runs; a divergence confirms the finding.
