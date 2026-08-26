# Q1256: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
wombat/WomUp.sol: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Under _totalSupply exceeds the mWOM balance the contract actually holds, is there an unprivileged sequence of `getReward()` that leaves `lastUpdateTime` unreconciled with `periodFinish`, violates the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `getReward()`: constrain the setup so that _totalSupply exceeds the mWOM balance the contract actually holds, fuzz the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller), and assert after every call that a participant must retain a way to realise an accrued reward without accepting a new lock commitment.
