# Q0520: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `stake(uint256 _amount)` while the attacker funds the stake with a flash loan of WOM repaid in the same transaction, and drive `lastUpdateTime` out of agreement with `periodFinish` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `stake(uint256 _amount)` sequence atomically under the attacker funds the stake with a flash loan of WOM repaid in the same transaction, asserting at the end that `lastUpdateTime` still equals `periodFinish` and the PoC's balance delta is non-positive.
