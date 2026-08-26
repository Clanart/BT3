# Q0414: WombatPoolHelper.depositLP - V1 exposes no depositFor so every credit is msg.sender

## Question
In wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Starting from a state where the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged EOA use `depositLP(uint256 _lpAmount)` to leave `this.balance(msg.sender)` inconsistent with `lockedAmount[msg.sender]`, violating the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount and the LP tokens pulled from the caller) under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting on every row that the single attribution path must still guarantee that minted receipts and credited stake are equal.
