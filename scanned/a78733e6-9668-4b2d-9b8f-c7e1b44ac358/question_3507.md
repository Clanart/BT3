# Q3507: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Note that in VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an attacker holding only tokens bought on market reach it via `lock(uint256 _amount)` under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `lock(uint256 _amount)` sequence atomically under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting at the end that `getUserAmountInCoolDown(user)` still equals `totalAmountInCoolDown` and the PoC's balance delta is non-positive.
