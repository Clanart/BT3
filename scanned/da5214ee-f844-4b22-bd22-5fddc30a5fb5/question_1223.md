# Q1223: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
rewards/ReferralStorage.sol: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, is there an unprivileged sequence of `useCode(bytes32 _code)` that leaves `refererPercentage + refereePercentage` unreconciled with `DENOMINATOR`, violates the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (which code is bound, and from which of the attacker's own addresses) under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, asserting on every row that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses.
