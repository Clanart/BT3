# Q0718: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Under the attacker's slot matured one block ago, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `getRewardablePercentWAD(user)` unreconciled with `_calExpireForfeit in mWOMSVBaseRewarder`, violates the invariant that every locked position must retain at least one reachable exit path under all reachable states, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker's slot matured one block ago.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker's slot matured one block ago, asserting at the end that `getRewardablePercentWAD(user)` still equals `_calExpireForfeit in mWOMSVBaseRewarder` and the PoC's balance delta is non-positive.
