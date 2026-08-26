# Q4110: WombatPoolHelper.depositNative - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol - WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` and the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `depositNative(uint256 _minimumLiquidity)` sequence atomically under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
