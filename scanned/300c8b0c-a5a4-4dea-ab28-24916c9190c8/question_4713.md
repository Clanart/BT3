# Q4713: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
In VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Starting from a state where the attacker repeats cancelUnlock and startUnlock inside a single transaction, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `userUnlockings[user][i].endTime` inconsistent with `block.timestamp`, violating the invariant that only the account itself may cause its locked balance and its derived governance weight to change and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats cancelUnlock and startUnlock inside a single transaction, call `lockFor(uint256 _amount, address _for)`, and assert `userUnlockings[user][i].endTime` equals `block.timestamp` and that no account can withdraw more than it put in.
