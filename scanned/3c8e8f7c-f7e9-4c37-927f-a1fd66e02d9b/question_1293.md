# Q1293: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker reach this through `lock(uint256 _amount)` while coolDownInSecs is at its configured production value and endTime is far in the future, and drive `totalPenalty` out of agreement with `IERC20(MGP).balanceOf(address(this))` - breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `lock(uint256 _amount)` sequence atomically under coolDownInSecs is at its configured production value and endTime is far in the future, asserting at the end that `totalPenalty` still equals `IERC20(MGP).balanceOf(address(this))` and the PoC's balance delta is non-positive.
