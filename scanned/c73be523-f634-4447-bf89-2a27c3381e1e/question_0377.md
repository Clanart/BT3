# Q0377: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Starting from a state where the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `getRewardablePercentWAD(user)` inconsistent with `_calExpireForfeit in mWOMSVBaseRewarder`, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `_calExpireForfeit in mWOMSVBaseRewarder` relation are unchanged by the attacker's transaction.
