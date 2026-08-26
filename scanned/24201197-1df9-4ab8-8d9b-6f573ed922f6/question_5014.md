# Q5014: WombatPoolHelper.withdraw - V1 exposes no depositFor so every credit is msg.sender

## Question
In wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
