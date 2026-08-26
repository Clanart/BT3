# Q0830: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
In wombat/WomUp.sol, getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Does `getReward()` let an unprivileged caller exploit that under the attacker funds the stake with a flash loan of WOM repaid in the same transaction, so that `rewards[account]` diverges from `IERC20(mgp).balanceOf(address(this))`, the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `getReward()`: constrain the setup so that the attacker funds the stake with a flash loan of WOM repaid in the same transaction, fuzz the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller), and assert after every call that a participant must retain a way to realise an accrued reward without accepting a new lock commitment.
