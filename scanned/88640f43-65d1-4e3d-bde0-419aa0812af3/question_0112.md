# Q0112: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
In rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an unprivileged attacker reach this through `useCode(bytes32 _code)` while the attacker controls two addresses and binds one to the other's code, and drive `userInfos[account].factor` out of agreement with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` - breaking the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (which code is bound, and from which of the attacker's own addresses) under the attacker controls two addresses and binds one to the other's code, asserting on every row that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses.
