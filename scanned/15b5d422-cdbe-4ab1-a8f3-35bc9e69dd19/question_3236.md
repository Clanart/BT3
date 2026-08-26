# Q3236: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the mWOM balance of the locker is exactly equal to totalAmount before the action, so that `getRewardablePercentWAD(user)` diverges from `_calExpireForfeit in mWOMSVBaseRewarder`, the invariant that every locked position must retain at least one reachable exit path under all reachable states is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the mWOM balance of the locker is exactly equal to totalAmount before the action, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getRewardablePercentWAD(user)` equals `_calExpireForfeit in mWOMSVBaseRewarder` and that no account can withdraw more than it put in.
