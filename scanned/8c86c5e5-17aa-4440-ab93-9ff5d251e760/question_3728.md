# Q3728: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
rewards/ReferralStorage.sol: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Under the attacker calls multiclaimFor on a set of referred accounts in one block, is there an unprivileged sequence of `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` that leaves `userInfos[account].factor` unreconciled with `totalBoostFactor`, violates the invariant that only the account itself may cause its boost factor to be recomputed, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the attacker calls multiclaimFor on a set of referred accounts in one block, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that only the account itself may cause its boost factor to be recomputed.
