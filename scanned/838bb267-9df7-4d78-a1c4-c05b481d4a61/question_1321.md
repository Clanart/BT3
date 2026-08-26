# Q1321: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
In VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Starting from a state where coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `userUnlockings[user][i].endTime` inconsistent with `block.timestamp`, violating the invariant that only the account itself may cause its locked balance and its derived governance weight to change and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that coolDownInSecs is at its configured production value and endTime is far in the future, fuzz the attacker inputs (_for (any victim address) and _amount, including one wei), and assert after every call that only the account itself may cause its locked balance and its derived governance weight to change.
