# Q2975: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the pool the attacker voted for has since been deactivated so unvote reverts, so that `totalAmount` diverges from `sum of userInfo[vlmgp][*].amount in MasterMagpie`, the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that the pool the attacker voted for has since been deactivated so unvote reverts, fuzz the attacker inputs (_slotIndex and how long after endTime the slot is redeemed), and assert after every call that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
