# Q0365: WomUp.getReward - getReward approves vlMGP without resetting

## Question
In wombat/WomUp.sol, getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Starting from a state where the attacker is the only staker for a single block, can an unprivileged EOA use `getReward()` to leave `_totalSupply` inconsistent with `IERC20(mWom).balanceOf(address(this))`, violating the invariant that the reward claim path must remain usable regardless of allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the attacker is the only staker for a single block.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward()` sequence atomically under the attacker is the only staker for a single block, asserting at the end that `_totalSupply` still equals `IERC20(mWom).balanceOf(address(this))` and the PoC's balance delta is non-positive.
