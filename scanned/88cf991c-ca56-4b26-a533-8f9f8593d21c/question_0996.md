# Q0996: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Consider VLMGP.sol, where _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Assuming the attacker's slot matured exactly one second ago, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` via `unlock(uint256 _slotIndex)`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and producing Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's slot matured exactly one second ago, snapshot `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)`, run the attacker's `unlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
