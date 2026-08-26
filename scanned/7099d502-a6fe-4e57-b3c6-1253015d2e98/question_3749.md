# Q3749: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol - unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an unprivileged attacker controlling _amount and the block in which the mWOM lock is credited, under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, exploit this through `lock(uint256 _amount)` to break the reconciliation between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` and the invariant that every locked position must retain at least one reachable exit path under all reachable states, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, have the attacker run `lock(uint256 _amount)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `_calExpireForfeit in mWOMSVBaseRewarder` relation are unchanged by the attacker's transaction.
