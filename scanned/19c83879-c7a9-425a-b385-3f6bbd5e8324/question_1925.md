# Q1925: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
wombat/WombatStaking.sol: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. With _lpToken and the timing of every harvest-driven fee split under attacker control and a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged caller sequence `harvest(address _lpToken)` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `isPoolFeeFree[_lpToken]` versus `feeInfos.length` relation are unchanged by the attacker's transaction.
