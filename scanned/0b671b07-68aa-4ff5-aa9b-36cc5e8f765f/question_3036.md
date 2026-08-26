# Q3036: WombatPoolHelper.depositNative - V1 exposes no depositFor so every credit is msg.sender

## Question
Consider wombat/WombatPoolHelper.sol, where WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Assuming the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (msg.value and _minimumLiquidity) under the caller sets _minAmount to zero on the withdrawal leg, asserting on every row that the single attribution path must still guarantee that minted receipts and credited stake are equal.
