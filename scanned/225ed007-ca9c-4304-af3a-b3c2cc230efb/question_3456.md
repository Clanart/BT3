# Q3456: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker reach this through `forceUnLock(uint256 _slotIndex)` while the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, and drive `totalPenalty` out of agreement with `IERC20(MGP).balanceOf(address(this))` - breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, call `forceUnLock(uint256 _slotIndex)`, and assert `totalPenalty` equals `IERC20(MGP).balanceOf(address(this))` and that no account can withdraw more than it put in.
