# Q5483: WombatPoolHelperV2.depositLP - deposit and withdraw both run the full harvest and fee path

## Question
wombat/WombatPoolHelperV2.sol - WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Can an unprivileged attacker controlling _lpAmount, under the receipt token is minted to the helper while the credit is directed at a different address, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` and the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the receipt token is minted to the helper while the credit is directed at a different address, have the attacker run `depositLP(uint256 _lpAmount)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
