# Q1139: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
Note that in wombat/WomUp.sol, migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Can an attacker holding only tokens bought on market reach it via `migrate(uint256 _amount, bool _claim, address _targetHelper)` under _totalSupply exceeds the mWOM balance the contract actually holds and force `_balances[account]` apart from `_totalSupply`, breaking the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange _totalSupply exceeds the mWOM balance the contract actually holds, call `migrate(uint256 _amount, bool _claim, address _targetHelper)`, and assert `_balances[account]` equals `_totalSupply` and that no account can withdraw more than it put in.
