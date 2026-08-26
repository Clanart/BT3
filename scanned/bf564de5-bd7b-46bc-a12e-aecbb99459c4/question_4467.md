# Q4467: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
Note that in VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit and force `userUnlockings[user][i].endTime` apart from `block.timestamp`, breaking the invariant that a third party must not be able to force a settlement checkpoint on another user's position for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, snapshot `userUnlockings[user][i].endTime` and `block.timestamp`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
