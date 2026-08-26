# Q0334: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `migrate(uint256 _amount, bool _claim, address _targetHelper)` while the attacker is the only staker for a single block, and drive `rewardRate * duration` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker is the only staker for a single block.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _claim and which allowlisted helper receives the position) under the attacker is the only staker for a single block, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
