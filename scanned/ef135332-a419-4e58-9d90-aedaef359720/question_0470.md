# Q0470: mWomSV.cancelUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. With _slotIndex and the moment the cooldown is aborted under attacker control and the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `userUnlockings[user][i].amountInCoolDown` and `maxSlot` no longer reconcile, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, snapshot `userUnlockings[user][i].amountInCoolDown` and `maxSlot`, run the attacker's `cancelUnlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
