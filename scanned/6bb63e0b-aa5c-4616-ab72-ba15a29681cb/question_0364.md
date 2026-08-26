# Q0364: MGPRelease.claim - the linear term truncates against a large denominator

## Question
rewards/MGPRelease.sol: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `beneficiaries[account].claimed` unreconciled with `getClaimable(account)`, violates the invariant that a linear release must not lose value to truncation across repeated claims, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the linear term truncates against a large denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: a linear release must not lose value to truncation across repeated claims; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under block.timestamp is exactly startTimestamp, asserting at the end that `beneficiaries[account].claimed` still equals `getClaimable(account)` and the PoC's balance delta is non-positive.
