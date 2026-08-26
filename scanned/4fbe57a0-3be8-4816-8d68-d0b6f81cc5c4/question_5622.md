# Q5622: WombatPoolHelper.depositNative - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol - WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the receipt token is minted to the helper while the credit is directed at a different address, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the receipt token is minted to the helper while the credit is directed at a different address, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
