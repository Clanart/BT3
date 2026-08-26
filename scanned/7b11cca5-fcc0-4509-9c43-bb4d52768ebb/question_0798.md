# Q0798: MGPRelease.claim - the linear term truncates against a large denominator

## Question
rewards/MGPRelease.sol: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `vested` and `beneficiaries[account].totalAlloced - initialUnlockedAmount` no longer reconcile, violating the invariant that a linear release must not lose value to truncation across repeated claims and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the linear term truncates against a large denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: a linear release must not lose value to truncation across repeated claims; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated) under the beneficiary claims repeatedly inside one block, asserting on every row that a linear release must not lose value to truncation across repeated claims.
