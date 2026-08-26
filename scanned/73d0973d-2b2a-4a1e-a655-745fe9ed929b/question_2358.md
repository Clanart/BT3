# Q2358: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
VLMGP.sol: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. With _for (any victim address) and _amount, including one wei under attacker control and the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged caller sequence `lockFor(uint256 _amount, address _for)` so that `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that a third party must not be able to force a settlement checkpoint on another user's position and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim address) and _amount, including one wei) under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting on every row that a third party must not be able to force a settlement checkpoint on another user's position.
