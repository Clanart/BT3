# Q4656: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
wombat/WombatStaking.sol - withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under the deposit token for the pool is wBNB and the helper arrived through depositNative, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` and the invariant that receipt-token supply must fall in the same transaction as the backing it represents, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(address,uint256,uint256,address) via a pool helper`: constrain the setup so that the deposit token for the pool is wBNB and the helper arrived through depositNative, fuzz the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw), and assert after every call that receipt-token supply must fall in the same transaction as the backing it represents.
