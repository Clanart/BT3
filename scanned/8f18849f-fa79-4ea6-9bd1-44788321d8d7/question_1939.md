# Q1939: WomUp.getReward - getReward approves vlMGP without resetting

## Question
wombat/WomUp.sol: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `getReward()` so that `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that the reward claim path must remain usable regardless of allowance residue and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the target helper leaves a non-zero allowance after depositFor, snapshot `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `getReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
