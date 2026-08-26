# Q2284: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
In wombat/WomUp.sol, getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Starting from a state where the attacker migrates and withdraws inside one transaction, can an unprivileged EOA use `getReward()` to leave `_totalSupply` inconsistent with `IERC20(mWom).balanceOf(address(this))`, violating the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `getReward()` sequence atomically under the attacker migrates and withdraws inside one transaction, asserting at the end that `_totalSupply` still equals `IERC20(mWom).balanceOf(address(this))` and the PoC's balance delta is non-positive.
