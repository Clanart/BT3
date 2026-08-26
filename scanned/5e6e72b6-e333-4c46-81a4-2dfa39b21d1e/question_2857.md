# Q2857: WomUp.getReward - getReward approves vlMGP without resetting

## Question
wombat/WomUp.sol: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Under the attacker calls getReward immediately after a large stake by another user, is there an unprivileged sequence of `getReward()` that leaves `rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[account]`, violates the invariant that the reward claim path must remain usable regardless of allowance residue, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward()` sequence atomically under the attacker calls getReward immediately after a large stake by another user, asserting at the end that `rewardPerTokenStored` still equals `userRewardPerTokenPaid[account]` and the PoC's balance delta is non-positive.
