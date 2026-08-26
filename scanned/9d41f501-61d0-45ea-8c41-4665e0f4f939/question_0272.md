# Q0272: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
In wombat/WomUp.sol, migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Starting from a state where the attacker is the only staker for a single block, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `lastUpdateTime` inconsistent with `periodFinish`, violating the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: the attacker is the only staker for a single block.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only staker for a single block, snapshot `lastUpdateTime` and `periodFinish`, run the attacker's `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
