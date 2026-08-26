# Q1898: VLMGP.lockFor - totalAmount and the MasterMagpie stake are written in different transactions

## Question
Note that in VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one and force `userTotalVotedInVlmgp(user) in WombatBribeManager` apart from `getUserTotalLocked(user)`, breaking the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, call `lockFor(uint256 _amount, address _for)`, and assert `userTotalVotedInVlmgp(user) in WombatBribeManager` equals `getUserTotalLocked(user)` and that no account can withdraw more than it put in.
