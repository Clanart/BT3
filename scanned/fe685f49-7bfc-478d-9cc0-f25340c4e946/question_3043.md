# Q3043: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol - stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the attacker stakes one wei so _totalSupply is non-zero but every division truncates, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `_balances[account]` and `_totalSupply` and the invariant that every function that mutates the stake ledger must share one reentrancy domain, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker stakes one wei so _totalSupply is non-zero but every division truncates, snapshot `_balances[account]` and `_totalSupply`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
