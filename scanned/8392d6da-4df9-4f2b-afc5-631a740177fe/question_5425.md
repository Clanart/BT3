# Q5425: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
In wombat/WombatStaking.sol, withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Does `withdraw(address,uint256,uint256,address) via a pool helper` let an unprivileged caller exploit that under the bonus reward token registered for the asset is also one of the fee currencies, so that `isPoolFeeFree[_lpToken]` diverges from `feeInfos.length`, the invariant that receipt-token supply must fall in the same transaction as the backing it represents is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under the bonus reward token registered for the asset is also one of the fee currencies, asserting at the end that `isPoolFeeFree[_lpToken]` still equals `feeInfos.length` and the PoC's balance delta is non-positive.
