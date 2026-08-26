# Q4077: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
wombat/WombatStaking.sol: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. With _lpToken and the timing of every harvest-driven fee split under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `harvest(address _lpToken)` so that `totalAccumulated in mWOM` and `veWom balance of WombatStaking` no longer reconcile, violating the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish several feeInfos entries are active at once and the harvested amount is small, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `totalAccumulated in mWOM` versus `veWom balance of WombatStaking` relation are unchanged by the attacker's transaction.
