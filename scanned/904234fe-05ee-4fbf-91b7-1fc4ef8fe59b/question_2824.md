# Q2824: VLMGP.lockFor - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol - _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker controlling _for (any victim address) and _amount, including one wei, under the pool the attacker voted for has since been deactivated so unvote reverts, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool the attacker voted for has since been deactivated so unvote reverts, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getUserTotalLocked(user)` versus `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` relation are unchanged by the attacker's transaction.
