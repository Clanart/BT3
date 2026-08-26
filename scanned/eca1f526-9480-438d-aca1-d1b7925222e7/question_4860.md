# Q4860: WombatPoolHelper.depositLP - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. With _lpAmount and the LP tokens pulled from the caller under attacker control and an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `depositLP(uint256 _lpAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
