# Q1603: WomUp.getReward - getReward approves vlMGP without resetting

## Question
wombat/WomUp.sol: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged caller sequence `getReward()` so that `lastUpdateTime` and `periodFinish` no longer reconcile, violating the invariant that the reward claim path must remain usable regardless of allowance residue and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller) under the reward period has just ended so periodFinish is behind block.timestamp, asserting on every row that the reward claim path must remain usable regardless of allowance residue.
