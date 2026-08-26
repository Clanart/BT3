# Q4014: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol - _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker controlling _slotIndex and how long after endTime the slot is redeemed, under a large vesting MGP distribution has just been queued into the vlMGP rewarder, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` and the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large vesting MGP distribution has just been queued into the vlMGP rewarder, call `unlock(uint256 _slotIndex)`, and assert `totalPenalty` equals `IERC20(MGP).balanceOf(address(this))` and that no account can withdraw more than it put in.
