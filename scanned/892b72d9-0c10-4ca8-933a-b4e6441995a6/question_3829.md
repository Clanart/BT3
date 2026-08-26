# Q3829: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Note that in wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder and force `mWomSV.getUserTotalLocked(user)` apart from `ArbWomUp3.calDoubledCounted(user)`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, call `startUnlock(uint256 _amountToCoolDown)`, and assert `mWomSV.getUserTotalLocked(user)` equals `ArbWomUp3.calDoubledCounted(user)` and that no account can withdraw more than it put in.
