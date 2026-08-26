# Q5301: WombatPoolHelper.harvest - V1 exposes no depositFor so every credit is msg.sender

## Question
In wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Does `harvest()` let an unprivileged caller exploit that under the attacker has moved the wom/mWom Wombat pool immediately before calling, so that `_liquidity burned via burnReceiptToken` diverges from `the deposit-token balance delta paid out by WombatStaking.withdraw`, the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `harvest()`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
