# Q3684: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Consider VLMGP.sol, where _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Assuming the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged attacker turn this into a divergence between `userUnlockings[user][i].endTime` and `block.timestamp` via `unlock(uint256 _slotIndex)`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and producing Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and how long after endTime the slot is redeemed) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
