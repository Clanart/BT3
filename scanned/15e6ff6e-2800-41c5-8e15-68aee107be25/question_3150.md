# Q3150: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. With _amount and the block in which the lock lands relative to an emission roll-forward under attacker control and the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, can an unprivileged caller sequence `lock(uint256 _amount)` so that `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` no longer reconcile, violating the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and realising Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` end identical in both runs.
