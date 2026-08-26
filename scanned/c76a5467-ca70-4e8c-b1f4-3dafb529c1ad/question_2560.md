# Q2560: WomUp.migrate - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming the MGP balance is below the sum of accrued rewards, can an unprivileged attacker turn this into a divergence between `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` via `migrate(uint256 _amount, bool _claim, address _targetHelper)`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the MGP balance is below the sum of accrued rewards, have the attacker run `migrate(uint256 _amount, bool _claim, address _targetHelper)`, then assert the victim's claimable value and the `rewardRate * duration` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
