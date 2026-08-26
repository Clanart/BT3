# Q4581: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Starting from a state where the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `userTotalVotedInVlmgp(user) in WombatBribeManager` inconsistent with `getUserTotalLocked(user)`, violating the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and extracting Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, then assert `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` end identical in both runs.
