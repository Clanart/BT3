# Q2004: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
In rewards/ReferralStorage.sol, VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Starting from a state where the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, can an unprivileged EOA use `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to leave `refererPercentage + refereePercentage` inconsistent with `DENOMINATOR`, violating the invariant that only the account itself may cause its boost factor to be recomputed and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that only the account itself may cause its boost factor to be recomputed.
