# Q1375: VLMGP.lockFor - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Starting from a state where coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `userInfos[user].factor in ReferralStorage` inconsistent with `getUserTotalLocked(user)`, violating the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and extracting Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that coolDownInSecs is at its configured production value and endTime is far in the future, fuzz the attacker inputs (_for (any victim address) and _amount, including one wei), and assert after every call that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point.
