# Q2204: WombatPoolHelper.depositLP - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. With _lpAmount and the LP tokens pulled from the caller under attacker control and the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` no longer reconcile, violating the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount and the LP tokens pulled from the caller) under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting on every row that the single attribution path must still guarantee that minted receipts and credited stake are equal.
