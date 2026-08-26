# Q3844: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Under a large vesting MGP distribution has just been queued into the vlMGP rewarder, is there an unprivileged sequence of `lock(uint256 _amount)` that leaves `totalAmount` unreconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`, violates the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, and delivers Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block in which the lock lands relative to an emission roll-forward) under a large vesting MGP distribution has just been queued into the vlMGP rewarder, asserting on every row that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
