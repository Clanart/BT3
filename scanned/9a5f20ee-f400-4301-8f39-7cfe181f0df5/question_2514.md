# Q2514: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
In wombat/WomUp.sol, migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Does `migrate(uint256 _amount, bool _claim, address _targetHelper)` let an unprivileged caller exploit that under the MGP balance is below the sum of accrued rewards, so that `lastUpdateTime` diverges from `periodFinish`, the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below the sum of accrued rewards, call `migrate(uint256 _amount, bool _claim, address _targetHelper)`, and assert `lastUpdateTime` equals `periodFinish` and that no account can withdraw more than it put in.
