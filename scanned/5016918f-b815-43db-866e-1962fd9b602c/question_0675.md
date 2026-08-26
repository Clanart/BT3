# Q0675: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol - stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the attacker funds the stake with a flash loan of WOM repaid in the same transaction, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` and the invariant that every function that mutates the stake ledger must share one reentrancy domain, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the stake with a flash loan of WOM repaid in the same transaction, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewardRate * duration` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
