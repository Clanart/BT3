# Q2169: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming the attacker migrates and withdraws inside one transaction, can an unprivileged attacker turn this into a divergence between `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` via `withdraw(uint256 amount, bool claim)`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker migrates and withdraws inside one transaction, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewards[account]` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
