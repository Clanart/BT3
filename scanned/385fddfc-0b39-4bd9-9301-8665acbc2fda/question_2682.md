# Q2682: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming the attacker calls getReward immediately after a large stake by another user, can an unprivileged attacker turn this into a divergence between `lastUpdateTime` and `periodFinish` via `stake(uint256 _amount)`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the attacker calls getReward immediately after a large stake by another user, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that every function that mutates the stake ledger must share one reentrancy domain.
