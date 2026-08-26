# Q4454: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
In VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, and drive `getRewardablePercentWAD(user)` out of agreement with `userUnlockings[user][i].amountInCoolDown` - breaking the invariant that only the account itself may cause its locked balance and its derived governance weight to change - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, snapshot `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
