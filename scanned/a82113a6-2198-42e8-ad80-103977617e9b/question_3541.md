# Q3541: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Note that in wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker repeats cancelUnlock and startUnlock inside one transaction and force `userUnlockings[user][i].amountInCoolDown` apart from `maxSlot`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker repeats cancelUnlock and startUnlock inside one transaction, asserting on every row that every locked position must retain at least one reachable exit path under all reachable states.
