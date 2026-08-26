# Q2307: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
Note that in wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an attacker holding only tokens bought on market reach it via `getReward()` under the attacker migrates and withdraws inside one transaction and force `rewardRate * duration` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker migrates and withdraws inside one transaction, then assert `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
