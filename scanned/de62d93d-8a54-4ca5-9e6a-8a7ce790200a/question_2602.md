# Q2602: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
wombat/WomUp.sol: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the MGP balance is below the sum of accrued rewards, can an unprivileged caller sequence `getReward()` so that `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` no longer reconcile, violating the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below the sum of accrued rewards, call `getReward()`, and assert `rewardPerTokenStored` equals `userRewardPerTokenPaid[account]` and that no account can withdraw more than it put in.
