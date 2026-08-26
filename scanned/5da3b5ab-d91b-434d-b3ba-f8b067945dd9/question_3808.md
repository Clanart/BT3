# Q3808: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
In rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an unprivileged attacker reach this through `useCode(bytes32 _code)` while sharePercent is set so most of the split goes to the referee, and drive `userInfos[account].rewardAmount` out of agreement with `MGP.balanceOf(address(this))` - breaking the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange sharePercent is set so most of the split goes to the referee, call `useCode(bytes32 _code)`, and assert `userInfos[account].rewardAmount` equals `MGP.balanceOf(address(this))` and that no account can withdraw more than it put in.
