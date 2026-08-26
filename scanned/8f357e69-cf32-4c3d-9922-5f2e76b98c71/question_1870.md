# Q1870: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
wombat/WomUp.sol: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. With _amount, _claim and which allowlisted helper receives the position under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `migrate(uint256 _amount, bool _claim, address _targetHelper)` so that `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` no longer reconcile, violating the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _claim and which allowlisted helper receives the position) under the target helper leaves a non-zero allowance after depositFor, asserting on every row that a migration must be atomic and must not be able to credit twice or debit without a matching credit.
