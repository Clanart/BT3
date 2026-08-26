# Q0848: WombatPoolHelper.withdraw - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Under the pool's deposit token is wBNB and the caller arrived through depositNative, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta) under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting on every row that the single attribution path must still guarantee that minted receipts and credited stake are equal.
