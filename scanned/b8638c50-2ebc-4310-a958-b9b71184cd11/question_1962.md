# Q1962: WomUp.getReward - reward is locked into vlMGP rather than paid liquid

## Question
wombat/WomUp.sol: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `getReward()` so that `_balances[account]` and `_totalSupply` no longer reconcile, violating the invariant that a participant must retain a way to realise an accrued reward without accepting a new lock commitment and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: reward is locked into vlMGP rather than paid liquid)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() routes the whole accrued amount through vlMGP.lockFor(reward, msg.sender), so claiming converts the reward into a cooldown-bound position, and the only alternative is not claiming at all. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: a participant must retain a way to realise an accrued reward without accepting a new lock commitment; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the target helper leaves a non-zero allowance after depositFor, then assert `_balances[account]` and `_totalSupply` end identical in both runs.
