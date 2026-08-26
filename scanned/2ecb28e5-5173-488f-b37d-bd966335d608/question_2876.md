# Q2876: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
In wombat/WomUp.sol, getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Starting from a state where the attacker calls getReward immediately after a large stake by another user, can an unprivileged EOA use `getReward()` to leave `rewards[account]` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker calls getReward immediately after a large stake by another user, then assert `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
