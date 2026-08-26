# Q1916: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
Note that in wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an attacker holding only tokens bought on market reach it via `migrate(uint256 _amount, bool _claim, address _targetHelper)` under the target helper leaves a non-zero allowance after depositFor and force `rewards[account]` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the target helper leaves a non-zero allowance after depositFor, then assert `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
