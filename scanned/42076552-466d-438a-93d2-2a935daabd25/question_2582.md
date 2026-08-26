# Q2582: WomUp.getReward - getReward approves vlMGP without resetting

## Question
wombat/WomUp.sol - getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Can an unprivileged attacker controlling the exact block at which accrued MGP is locked into vlMGP for the caller, under the MGP balance is below the sum of accrued rewards, exploit this through `getReward()` to break the reconciliation between `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` and the invariant that the reward claim path must remain usable regardless of allowance residue, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below the sum of accrued rewards, call `getReward()`, and assert `_totalSupply` equals `IERC20(mWom).balanceOf(address(this))` and that no account can withdraw more than it put in.
