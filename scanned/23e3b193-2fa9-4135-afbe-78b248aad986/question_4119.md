# Q4119: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Under a large vesting MGP distribution has just been queued into the vlMGP rewarder, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `userTotalVotedInVlmgp(user) in WombatBribeManager` unreconciled with `getUserTotalLocked(user)`, violates the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, and delivers Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large vesting MGP distribution has just been queued into the vlMGP rewarder, call `forceUnLock(uint256 _slotIndex)`, and assert `userTotalVotedInVlmgp(user) in WombatBribeManager` equals `getUserTotalLocked(user)` and that no account can withdraw more than it put in.
