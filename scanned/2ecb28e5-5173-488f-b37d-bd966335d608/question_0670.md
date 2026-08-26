# Q0670: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
Note that in rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an attacker holding only tokens bought on market reach it via `useCode(bytes32 _code)` under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor and force `userInfos[account].rewardAmount` apart from `MGP.balanceOf(address(this))`, breaking the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, have the attacker run `useCode(bytes32 _code)`, then assert the victim's claimable value and the `userInfos[account].rewardAmount` versus `MGP.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
