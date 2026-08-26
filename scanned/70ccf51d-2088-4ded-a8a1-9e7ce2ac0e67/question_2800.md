# Q2800: WomUp.migrate - migrate reduces the ledger and pushes value to an allowlisted helper

## Question
In wombat/WomUp.sol, migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Starting from a state where the attacker calls getReward immediately after a large stake by another user, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `rewardRate * duration` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that a migration must be atomic and must not be able to credit twice or debit without a matching credit and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate reduces the ledger and pushes value to an allowlisted helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() debits _balances[msg.sender] and then approves and calls ISimpleHelper(_targetHelper).depositFor(_amount, msg.sender), so the position leaves this ledger and lands in MasterMagpie through a second contract in the same transaction. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: a migration must be atomic and must not be able to credit twice or debit without a matching credit; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls getReward immediately after a large stake by another user, call `migrate(uint256 _amount, bool _claim, address _targetHelper)`, and assert `rewardRate * duration` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
