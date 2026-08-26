# Q1589: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
wombat/WombatStaking.sol: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` no longer reconcile, violating the invariant that receipt-token supply must fall in the same transaction as the backing it represents and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(address,uint256,uint256,address) via a pool helper`: constrain the setup so that the contract is holding WOM collected as a protocol fee that has not yet been split, fuzz the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw), and assert after every call that receipt-token supply must fall in the same transaction as the backing it represents.
