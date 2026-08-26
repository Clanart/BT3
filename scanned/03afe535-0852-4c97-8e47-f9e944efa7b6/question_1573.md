# Q1573: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
rewards/ReferralStorage.sol - VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` and the invariant that only the account itself may cause its boost factor to be recomputed, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, asserting at the end that `userInfos[account].rewardAmount` still equals `MGP.balanceOf(address(this))` and the PoC's balance delta is non-positive.
