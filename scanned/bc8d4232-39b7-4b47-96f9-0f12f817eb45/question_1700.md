# Q1700: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
rewards/ReferralStorage.sol - useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an unprivileged attacker controlling which code is bound, and from which of the attacker's own addresses, under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, exploit this through `useCode(bytes32 _code)` to break the reconciliation between `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` and the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, call `useCode(bytes32 _code)`, and assert `tiers[tierId].rewardPercentage + _calBoosted(referer)` equals `DENOMINATOR` and that no account can withdraw more than it put in.
