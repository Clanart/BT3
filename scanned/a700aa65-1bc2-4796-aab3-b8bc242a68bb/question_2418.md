# Q2418: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
rewards/ReferralStorage.sol: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. With the target account, because lockFor is permissionless under attacker control and the attacker locked vlMGP before registering a code, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` no longer reconcile, violating the invariant that only the account itself may cause its boost factor to be recomputed and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker locked vlMGP before registering a code, asserting at the end that `tiers[tierId].rewardPercentage + _calBoosted(referer)` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
