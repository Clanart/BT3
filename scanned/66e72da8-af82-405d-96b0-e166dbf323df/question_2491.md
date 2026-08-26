# Q2491: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol - stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the MGP balance is below the sum of accrued rewards, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `lastUpdateTime` and `periodFinish` and the invariant that every function that mutates the stake ledger must share one reentrancy domain, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (amount and whether the claim leg runs in the same call) under the MGP balance is below the sum of accrued rewards, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
