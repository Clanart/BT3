# Q1612: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Starting from a state where the attacker arrived through SmartWomConvert.convertFor with _mode == 2, can an unprivileged EOA use `lock(uint256 _amount)` to leave `getRewardablePercentWAD(user)` inconsistent with `_calExpireForfeit in mWOMSVBaseRewarder`, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `lock(uint256 _amount)` sequence atomically under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, asserting at the end that `getRewardablePercentWAD(user)` still equals `_calExpireForfeit in mWOMSVBaseRewarder` and the PoC's balance delta is non-positive.
