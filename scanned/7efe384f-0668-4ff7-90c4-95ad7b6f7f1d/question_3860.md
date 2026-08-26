# Q3860: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
Note that in VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under a large vesting MGP distribution has just been queued into the vlMGP rewarder and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that only the account itself may cause its locked balance and its derived governance weight to change for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim address) and _amount, including one wei) under a large vesting MGP distribution has just been queued into the vlMGP rewarder, asserting on every row that only the account itself may cause its locked balance and its derived governance weight to change.
