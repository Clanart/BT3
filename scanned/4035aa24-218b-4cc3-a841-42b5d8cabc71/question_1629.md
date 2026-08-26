# Q1629: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
wombat/WomUp.sol - getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Can an unprivileged attacker controlling the exact block at which accrued MGP is locked into vlMGP for the caller, under the reward period has just ended so periodFinish is behind block.timestamp, exploit this through `getReward()` to break the reconciliation between `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` and the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the reward period has just ended so periodFinish is behind block.timestamp, have the attacker run `getReward()`, then assert the victim's claimable value and the `rewardRate * duration` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
