# Q3787: WombatPoolHelper.harvest - V1 exposes no depositFor so every credit is msg.sender

## Question
In wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `harvest()` to leave `IERC20(stakingToken).totalSupply()` inconsistent with `the MasterWombat staked balance for pid`, violating the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that a residual stakingToken balance from an earlier rounding sits on the helper, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
