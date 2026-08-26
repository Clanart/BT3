# Q1847: WomUp.withdraw - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while the target helper leaves a non-zero allowance after depositFor, and drive `rewardPerTokenStored` out of agreement with `userRewardPerTokenPaid[account]` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 amount, bool claim)` sequence atomically under the target helper leaves a non-zero allowance after depositFor, asserting at the end that `rewardPerTokenStored` still equals `userRewardPerTokenPaid[account]` and the PoC's balance delta is non-positive.
