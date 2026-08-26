# Q1759: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Under coolDownInSecs is at its configured production value and endTime is far in the future, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `getUserAmountInCoolDown(user)` unreconciled with `totalAmountInCoolDown`, violates the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, and delivers Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange coolDownInSecs is at its configured production value and endTime is far in the future, call `forceUnLock(uint256 _slotIndex)`, and assert `getUserAmountInCoolDown(user)` equals `totalAmountInCoolDown` and that no account can withdraw more than it put in.
