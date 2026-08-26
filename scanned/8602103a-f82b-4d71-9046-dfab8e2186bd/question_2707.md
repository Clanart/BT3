# Q2707: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Starting from a state where the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `getRewardablePercentWAD(user)` inconsistent with `userUnlockings[user][i].amountInCoolDown`, violating the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and extracting Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting on every row that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
