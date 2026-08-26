# Q5222: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
Consider wombat/WombatStaking.sol, where withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Assuming a large honest deposit is pending in the mempool for the same pool, can an unprivileged attacker turn this into a divergence between `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that receipt-token supply must fall in the same transaction as the backing it represents and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large honest deposit is pending in the mempool for the same pool, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
