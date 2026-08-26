# Q3129: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
In rewards/ReferralStorage.sol, VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Can an unprivileged attacker reach this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` while the attacker splits one large lock across many addresses that each register a code, and drive `myReferer[account]` out of agreement with `userInfos[account].codeIUsed` - breaking the invariant that only the account itself may cause its boost factor to be recomputed - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker splits one large lock across many addresses that each register a code, then assert `myReferer[account]` and `userInfos[account].codeIUsed` end identical in both runs.
