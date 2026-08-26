# Q3435: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
Consider rewards/ReferralStorage.sol, where VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Assuming the referee has a large pending MGP claim in MasterMagpie, can an unprivileged attacker turn this into a divergence between `BoostPoint` and `totalBoostFactor` via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, breaking the invariant that only the account itself may cause its boost factor to be recomputed and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the referee has a large pending MGP claim in MasterMagpie, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that only the account itself may cause its boost factor to be recomputed.
