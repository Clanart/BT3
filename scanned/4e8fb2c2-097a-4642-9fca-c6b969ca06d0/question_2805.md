# Q2805: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
In VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the pool the attacker voted for has since been deactivated so unvote reverts, and drive `maxSlot` out of agreement with `userUnlockings[user].length` - breaking the invariant that a third party must not be able to force a settlement checkpoint on another user's position - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim address) and _amount, including one wei) under the pool the attacker voted for has since been deactivated so unvote reverts, asserting on every row that a third party must not be able to force a settlement checkpoint on another user's position.
