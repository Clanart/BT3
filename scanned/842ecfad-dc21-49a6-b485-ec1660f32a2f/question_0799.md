# Q0799: WomUp.getReward - getReward approves vlMGP without resetting

## Question
In wombat/WomUp.sol, getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Does `getReward()` let an unprivileged caller exploit that under the attacker funds the stake with a flash loan of WOM repaid in the same transaction, so that `rewardPerTokenStored` diverges from `userRewardPerTokenPaid[account]`, the invariant that the reward claim path must remain usable regardless of allowance residue is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the stake with a flash loan of WOM repaid in the same transaction, have the attacker run `getReward()`, then assert the victim's claimable value and the `rewardPerTokenStored` versus `userRewardPerTokenPaid[account]` relation are unchanged by the attacker's transaction.
