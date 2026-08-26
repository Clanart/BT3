# Q0346: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
Note that in wombat/mWomSV.sol, VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18 and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, asserting on every row that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls.
