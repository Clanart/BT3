# Q1577: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Starting from a state where the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[account]`, violating the invariant that every function that mutates the stake ledger must share one reentrancy domain and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _claim and which allowlisted helper receives the position) under the reward period has just ended so periodFinish is behind block.timestamp, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
