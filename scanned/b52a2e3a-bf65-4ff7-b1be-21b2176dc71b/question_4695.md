# Q4695: WombatPoolHelper.harvest - V1 exposes no depositFor so every credit is msg.sender

## Question
wombat/WombatPoolHelper.sol: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, is there an unprivileged sequence of `harvest()` that leaves `pid cached at construction` unreconciled with `pools[lpToken].pid in WombatStaking`, violates the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
