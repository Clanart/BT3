# Q4164: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, is there an unprivileged sequence of `lock(uint256 _amount)` that leaves `getRewardablePercentWAD(user)` unreconciled with `userUnlockings[user][i].amountInCoolDown`, violates the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, and delivers Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, have the attacker run `lock(uint256 _amount)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `userUnlockings[user][i].amountInCoolDown` relation are unchanged by the attacker's transaction.
