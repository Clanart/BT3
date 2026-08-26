# Q3151: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Note that in wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an attacker holding only tokens bought on market reach it via `lock(uint256 _amount)` under the mWOM balance of the locker is exactly equal to totalAmount before the action and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the mWOM balance of the locker is exactly equal to totalAmount before the action, snapshot `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown`, run the attacker's `lock(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
