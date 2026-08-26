# Q0005: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol - unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an unprivileged attacker controlling _amount and the block in which the mWOM lock is credited, under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `lock(uint256 _amount)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the invariant that every locked position must retain at least one reachable exit path under all reachable states, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `lock(uint256 _amount)` sequence atomically under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the PoC's balance delta is non-positive.
