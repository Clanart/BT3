# Q4824: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
wombat/WombatStaking.sol - _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the attacker deposits and withdraws through the same helper inside one transaction, exploit this through `harvest(address _lpToken)` to break the reconciliation between `feeInfos[i].value` and `totalFee` and the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker deposits and withdraws through the same helper inside one transaction, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `feeInfos[i].value` versus `totalFee` relation are unchanged by the attacker's transaction.
