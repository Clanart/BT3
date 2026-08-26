# Q3355: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the mWOM balance of the locker is exactly equal to totalAmount before the action, so that `userUnlockings[user][i].amountInCoolDown` diverges from `maxSlot`, the invariant that every locked position must retain at least one reachable exit path under all reachable states is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the mWOM balance of the locker is exactly equal to totalAmount before the action, call `unlock(uint256 _slotIndex)`, and assert `userUnlockings[user][i].amountInCoolDown` equals `maxSlot` and that no account can withdraw more than it put in.
