# Q1109: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
Note that in wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 amount, bool claim)` under _totalSupply exceeds the mWOM balance the contract actually holds and force `_balances[account]` apart from `_totalSupply`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (amount and whether the claim leg runs in the same call) under _totalSupply exceeds the mWOM balance the contract actually holds, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
