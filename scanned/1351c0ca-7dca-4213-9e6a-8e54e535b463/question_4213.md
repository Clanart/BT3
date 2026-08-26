# Q4213: WombatPoolHelper.withdraw - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol - WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an unprivileged attacker controlling _liquidity and _minAmount, with the payout measured as a balance delta, under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, fuzz the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
