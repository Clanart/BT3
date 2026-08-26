# Q1228: WomUp.getReward - getReward approves vlMGP without resetting

## Question
Consider wombat/WomUp.sol, where getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Assuming _totalSupply exceeds the mWOM balance the contract actually holds, can an unprivileged attacker turn this into a divergence between `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` via `getReward()`, breaking the invariant that the reward claim path must remain usable regardless of allowance residue and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange _totalSupply exceeds the mWOM balance the contract actually holds, call `getReward()`, and assert `rewards[account]` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
