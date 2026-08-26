# Q3573: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
wombat/mWomSV.sol: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. With _amountToCoolDown and the timestamps written into the slot under attacker control and the attacker repeats cancelUnlock and startUnlock inside one transaction, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` no longer reconcile, violating the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker repeats cancelUnlock and startUnlock inside one transaction, asserting on every row that the locked-balance accessor must never revert, and a user must always be able to read and exit.
