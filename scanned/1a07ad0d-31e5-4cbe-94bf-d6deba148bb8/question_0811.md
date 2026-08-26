# Q0811: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
wombat/mWomSV.sol: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. With _amountToCoolDown and the timestamps written into the slot under attacker control and the attacker's slot matured one block ago, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that cooldown already served must not be transferable to a newly committed amount and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the attacker's slot matured one block ago.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker's slot matured one block ago, asserting at the end that `totalAmount` still equals `IERC20(mWOM).balanceOf(address(this))` and the PoC's balance delta is non-positive.
