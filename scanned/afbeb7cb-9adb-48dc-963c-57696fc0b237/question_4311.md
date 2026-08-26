# Q4311: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, so that `userInfos[user].factor in ReferralStorage` diverges from `getUserTotalLocked(user)`, the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userInfos[user].factor in ReferralStorage` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
