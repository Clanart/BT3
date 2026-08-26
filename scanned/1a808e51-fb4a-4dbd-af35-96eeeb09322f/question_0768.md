# Q0768: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming the attacker funds the stake with a flash loan of WOM repaid in the same transaction, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `_totalSupply` via `migrate(uint256 _amount, bool _claim, address _targetHelper)`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the stake with a flash loan of WOM repaid in the same transaction, snapshot `_balances[account]` and `_totalSupply`, run the attacker's `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
