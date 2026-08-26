# Q1015: MGPRelease.claim - the linear term truncates against a large denominator

## Question
Consider rewards/MGPRelease.sol, where vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))` via `claim()`, breaking the invariant that a linear release must not lose value to truncation across repeated claims and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the linear term truncates against a large denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: a linear release must not lose value to truncation across repeated claims; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract balance is below the sum of unclaimed allocations, snapshot `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
