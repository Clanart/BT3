# Q0035: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
In VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Does `lockFor(uint256 _amount, address _for)` let an unprivileged caller exploit that under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, so that `totalAmount` diverges from `sum of userInfo[vlmgp][*].amount in MasterMagpie`, the invariant that only the account itself may cause its locked balance and its derived governance weight to change is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, fuzz the attacker inputs (_for (any victim address) and _amount, including one wei), and assert after every call that only the account itself may cause its locked balance and its derived governance weight to change.
