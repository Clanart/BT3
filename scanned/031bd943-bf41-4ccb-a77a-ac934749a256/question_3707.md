# Q3707: WombatPoolHelper.withdraw - V1 exposes no depositFor so every credit is msg.sender

## Question
Consider wombat/WombatPoolHelper.sol, where WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Assuming a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta) under a residual stakingToken balance from an earlier rounding sits on the helper, asserting on every row that the single attribution path must still guarantee that minted receipts and credited stake are equal.
