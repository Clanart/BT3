# Q4085: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
In rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Does `useCode(bytes32 _code)` let an unprivileged caller exploit that under sharePercent is set so most of the split goes to the referrer, so that `refererPercentage + refereePercentage` diverges from `DENOMINATOR`, the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (which code is bound, and from which of the attacker's own addresses) under sharePercent is set so most of the split goes to the referrer, asserting on every row that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses.
