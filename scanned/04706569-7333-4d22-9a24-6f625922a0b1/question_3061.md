# Q3061: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
In wombat/WomUp.sol, migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Can an unprivileged attacker reach this through `migrate(uint256 _amount, bool _claim, address _targetHelper)` while the attacker stakes one wei so _totalSupply is non-zero but every division truncates, and drive `_balances[account]` out of agreement with `_totalSupply` - breaking the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence atomically under the attacker stakes one wei so _totalSupply is non-zero but every division truncates, asserting at the end that `_balances[account]` still equals `_totalSupply` and the PoC's balance delta is non-positive.
