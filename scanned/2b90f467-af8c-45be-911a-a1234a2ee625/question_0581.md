# Q0581: MGPRelease.claim - the linear term truncates against a large denominator

## Question
In rewards/MGPRelease.sol, vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `initialUnlockedAmount` out of agreement with `beneficiaries[account].claimed` - breaking the invariant that a linear release must not lose value to truncation across repeated claims - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the linear term truncates against a large denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: a linear release must not lose value to truncation across repeated claims; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is exactly endTimestamp, have the attacker run `claim()`, then assert the victim's claimable value and the `initialUnlockedAmount` versus `beneficiaries[account].claimed` relation are unchanged by the attacker's transaction.
