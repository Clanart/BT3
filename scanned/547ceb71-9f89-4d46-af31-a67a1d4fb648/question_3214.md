# Q3214: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
Note that in rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an attacker holding only tokens bought on market reach it via `useCode(bytes32 _code)` under the referee has a large pending MGP claim in MasterMagpie and force `userInfos[account].factor` apart from `totalBoostFactor`, breaking the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the referee has a large pending MGP claim in MasterMagpie, snapshot `userInfos[account].factor` and `totalBoostFactor`, run the attacker's `useCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
