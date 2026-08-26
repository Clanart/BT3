# Q2238: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `migrate(uint256 _amount, bool _claim, address _targetHelper)` while the attacker migrates and withdraws inside one transaction, and drive `lastUpdateTime` out of agreement with `periodFinish` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence atomically under the attacker migrates and withdraws inside one transaction, asserting at the end that `lastUpdateTime` still equals `periodFinish` and the PoC's balance delta is non-positive.
