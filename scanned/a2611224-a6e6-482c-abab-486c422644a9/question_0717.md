# Q0717: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
In VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker's slot matured exactly one second ago, and drive `userUnlockings[user][i].endTime` out of agreement with `block.timestamp` - breaking the invariant that a third party must not be able to force a settlement checkpoint on another user's position - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's slot matured exactly one second ago, snapshot `userUnlockings[user][i].endTime` and `block.timestamp`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
