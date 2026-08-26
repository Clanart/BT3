# Q1876: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
In wombat/mWomSV.sol, VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker arrived through SmartWomConvert.convertFor with _mode == 2, and drive `userUnlockings[user][i].amountInCoolDown` out of agreement with `maxSlot` - breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, asserting on every row that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls.
