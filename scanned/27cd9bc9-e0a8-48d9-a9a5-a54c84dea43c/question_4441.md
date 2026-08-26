# Q4441: VLMGP.lock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
VLMGP.sol - _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an unprivileged attacker controlling _amount and the block in which the lock lands relative to an emission roll-forward, under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, exploit this through `lock(uint256 _amount)` to break the reconciliation between `userUnlockings[user][i].endTime` and `block.timestamp` and the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lock(uint256 _amount)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the lock lands relative to an emission roll-forward
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, then assert `userUnlockings[user][i].endTime` and `block.timestamp` end identical in both runs.
