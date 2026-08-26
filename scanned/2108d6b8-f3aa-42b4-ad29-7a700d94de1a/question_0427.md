# Q0427: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
Note that in wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an attacker holding only tokens bought on market reach it via `getReward()` under the attacker is the only staker for a single block and force `_balances[account]` apart from `_totalSupply`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker is the only staker for a single block.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only staker for a single block, call `getReward()`, and assert `_balances[account]` equals `_totalSupply` and that no account can withdraw more than it put in.
