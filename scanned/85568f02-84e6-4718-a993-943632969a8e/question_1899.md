# Q1899: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, so that `getUserTotalLocked(user)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, the invariant that every locked position must retain at least one reachable exit path under all reachable states is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the PoC's balance delta is non-positive.
