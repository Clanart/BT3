# Q4288: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
wombat/WombatStaking.sol - withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under several feeInfos entries are active at once and the harvested amount is small, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` and the invariant that receipt-token supply must fall in the same transaction as the backing it represents, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under several feeInfos entries are active at once and the harvested amount is small, asserting at the end that `totalAccumulated in mWOM` still equals `veWom balance of WombatStaking` and the PoC's balance delta is non-positive.
