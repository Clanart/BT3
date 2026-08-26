# Q5399: WombatPoolHelper.depositLP - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol - WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an unprivileged attacker controlling _lpAmount and the LP tokens pulled from the caller, under the attacker deposits and withdraws through the helper inside one transaction, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` and the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that the attacker deposits and withdraws through the helper inside one transaction, fuzz the attacker inputs (_lpAmount and the LP tokens pulled from the caller), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
