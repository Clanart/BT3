# Q1829: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker reach this through `lock(uint256 _amount)` while the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, and drive `userInfos[user].factor in ReferralStorage` out of agreement with `getUserTotalLocked(user)` - breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `lock(uint256 _amount)`: constrain the setup so that the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, fuzz the attacker inputs (_amount and the block in which the lock lands relative to an emission roll-forward), and assert after every call that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
