# Q2895: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming the attacker calls getReward immediately after a large stake by another user, can an unprivileged attacker turn this into a divergence between `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` via `getReward()`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls getReward immediately after a large stake by another user, have the attacker run `getReward()`, then assert the victim's claimable value and the `_totalSupply` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
