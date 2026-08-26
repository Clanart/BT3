# Q0562: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Consider VLMGP.sol, where _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Assuming the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, can an unprivileged attacker turn this into a divergence between `maxSlot` and `userUnlockings[user].length` via `forceUnLock(uint256 _slotIndex)`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and producing Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, asserting on every row that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
