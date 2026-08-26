# Q4725: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
In VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker repeats cancelUnlock and startUnlock inside a single transaction, and drive `totalPenalty` out of agreement with `IERC20(MGP).balanceOf(address(this))` - breaking the invariant that a third party must not be able to force a settlement checkpoint on another user's position - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats cancelUnlock and startUnlock inside a single transaction, snapshot `totalPenalty` and `IERC20(MGP).balanceOf(address(this))`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
