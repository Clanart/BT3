# Q3892: VLMGP.lockFor - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while a large vesting MGP distribution has just been queued into the vlMGP rewarder, and drive `getRewardablePercentWAD(user)` out of agreement with `userUnlockings[user][i].amountInCoolDown` - breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish a large vesting MGP distribution has just been queued into the vlMGP rewarder, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `userUnlockings[user][i].amountInCoolDown` relation are unchanged by the attacker's transaction.
