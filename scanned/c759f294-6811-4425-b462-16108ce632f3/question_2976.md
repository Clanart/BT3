# Q2976: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
Note that in wombat/mWomSV.sol, getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker holds a second address so lockFor can be used across two accounts and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that cooldown already served must not be transferable to a newly committed amount for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker holds a second address so lockFor can be used across two accounts, asserting at the end that `getUserAmountInCoolDown(user)` still equals `totalAmountInCoolDown` and the PoC's balance delta is non-positive.
