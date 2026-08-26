# Q3796: VLMGP.forceUnLock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol - _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker controlling _slotIndex and the exact point inside the cooldown curve at which the penalty is priced, under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, exploit this through `forceUnLock(uint256 _slotIndex)` to break the reconciliation between `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` and the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
