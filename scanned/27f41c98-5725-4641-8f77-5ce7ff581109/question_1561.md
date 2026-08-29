# Q1561: receive-underlying via accrue: make a required price path abort so the position can no lo

## Question
Can an unprivileged attacker entering through `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835), controlling the utilization the rate is interpolated at, drive `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) — which pulls the underlying from a named account — to make a required price path abort so the position can no longer be closed or seized, breaking the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction, and cause direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: the utilization the rate is interpolated at
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `accrue` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `accrue` with the utilization the rate is interpolated at, then read `receive-underlying` state before and after in the same block and assert the two sides of the invariant are equal.
