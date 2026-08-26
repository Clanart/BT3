# Q0655: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Consider VLMGP.sol, where _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Assuming the attacker's slot matured exactly one second ago, can an unprivileged attacker turn this into a divergence between `userUnlockings[user][i].endTime` and `block.timestamp` via `lock(uint256 _amount)`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and producing Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `lock(uint256 _amount)`: constrain the setup so that the attacker's slot matured exactly one second ago, fuzz the attacker inputs (_amount and the block in which the lock lands relative to an emission roll-forward), and assert after every call that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
