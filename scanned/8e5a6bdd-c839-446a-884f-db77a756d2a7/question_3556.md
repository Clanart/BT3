# Q3556: VLMGP.lockFor - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, and drive `totalAmount` out of agreement with `sum of userInfo[vlmgp][*].amount in MasterMagpie` - breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `lockFor(uint256 _amount, address _for)` sequence atomically under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting at the end that `totalAmount` still equals `sum of userInfo[vlmgp][*].amount in MasterMagpie` and the PoC's balance delta is non-positive.
