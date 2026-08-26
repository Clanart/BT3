# Q1199: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. With _amount, _claim and which allowlisted helper receives the position under attacker control and _totalSupply exceeds the mWOM balance the contract actually holds, can an unprivileged caller sequence `migrate(uint256 _amount, bool _claim, address _targetHelper)` so that `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that every function that mutates the stake ledger must share one reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish _totalSupply exceeds the mWOM balance the contract actually holds, have the attacker run `migrate(uint256 _amount, bool _claim, address _targetHelper)`, then assert the victim's claimable value and the `_totalSupply` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
