# Q2799: ReferralStorage.updateTotalFactor - updateTotalFactor can be driven for any account through permissionless lockFor

## Question
Note that in rewards/ReferralStorage.sol, VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Can an attacker holding only tokens bought on market reach it via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` under the attacker cancels a cooldown so their real lock rises with no factor refresh and force `codeOwners[_code]` apart from `userInfos[account].myCode`, breaking the invariant that only the account itself may cause its boost factor to be recomputed for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: updateTotalFactor can be driven for any account through permissionless lockFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: VLMGP.lockFor is permissionless and calls updateTotalFactor(_for), so a one-wei lock lets an attacker force a factor refresh on any account at a chosen instant. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: only the account itself may cause its boost factor to be recomputed; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker cancels a cooldown so their real lock rises with no factor refresh, call `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
