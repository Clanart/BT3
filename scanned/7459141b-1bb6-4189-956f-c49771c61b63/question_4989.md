# Q4989: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
wombat/WombatStaking.sol: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and the attacker deposits and withdraws through the same helper inside one transaction, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `feeInfos[i].value` and `totalFee` no longer reconcile, violating the invariant that receipt-token supply must fall in the same transaction as the backing it represents and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker deposits and withdraws through the same helper inside one transaction, snapshot `feeInfos[i].value` and `totalFee`, run the attacker's `withdraw(address,uint256,uint256,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
