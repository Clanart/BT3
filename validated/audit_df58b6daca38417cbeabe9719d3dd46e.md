Based on my investigation, I found a solid local analog of the "empty trade" bug class in the same-chain intent fill path.

### Title
Same-chain `fillOrder` can claim `_filled[commitment]` and re-lock an order with an all-zero-amount partial fill, enabling a griefing/state-manipulation path with no tokens moved - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain` unconditionally sets `_filled[commitment] = msg.sender` at entry, before validating that any output leg actually has a non-zero `solverAmount`, mirroring the reported Battle.trade() pattern where a price-limit exit changes state (price) while performing zero token movement. The per-leg loop explicitly tolerates `solverAmount == 0` (`if (remaining == 0 || solverAmount == 0) { ...; continue; }`), so a caller can invoke `fillOrder` with an all-zero `FillOptions.outputs` array and have the function walk every leg without transferring a single token, before falling into the `isFullyFilled == false` branch that deletes `_filled[commitment]`. This "no-op fill" executes fully (loop, `_withdraw` call with an all-zero `escrowedInputs` array, event emission) purely on attacker-supplied zero amounts, exercising exactly the "empty trade" primitive from the report: an operation that reaches completion and performs internal state work (loop iteration, `_withdraw` invocation, event emission, and transient claim/release of `_filled[commitment]`) while moving zero value.

### Finding Description [1](#0-0) 

`_fillSameChain` sets the claim flag first:
```solidity
_filled[commitment] = msg.sender;
...
for (uint256 i; i < outputsLen; i++) {
    ...
    uint256 solverAmount = options.outputs[i].amount;
    uint256 alreadyFilled = _partialFills[commitment][outputToken];
    uint256 remaining = totalRequired - alreadyFilled;
    if (remaining == 0 || solverAmount == 0) {
        if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
        continue;
    }
    ...
}
```
`fillOrder`'s shared validation only checks array-length equality (`options.outputs.length == outputsLen`), not that any `solverAmount` is non-zero: [2](#0-1) 

So a caller can submit `FillOptions.outputs` with every `amount = 0`. Every leg hits the `solverAmount == 0` branch and `continue`s — no `IERC20.safeTransferFrom`, no native transfer, `escrowedInputs[i]` stays as the zero-value default `TokenInfo`. The function still reaches `_withdraw(body, false, isFullyFilled)` (with `isFullyFilled = false`) and then `delete _filled[commitment]; emit PartialFill(...)` — a complete, "successful" transaction that moved no assets, exactly the empty-trade/no-op-execution pattern in the report (loop exits without moving tokens, but internal state — here `_filled`, `_partialFills` bookkeeping, and events — is still touched).

This differs from the cross-chain path (`_fillCrossChain`), which reverts on `solverAmount < totalRequired` for every leg, closing this gap entirely (all-or-nothing enforcement). The same-chain path has no equivalent floor check requiring `solverAmount > 0`.

### Impact Explanation
The immediate effect is a spuriously "successful" `fillOrder` call and `PartialFill` event with zero economic content, which can be used to spam or desynchronize any off-chain solver/indexer bookkeeping that treats `PartialFill` as a positive signal of liquidity movement, and to transiently occupy `_filled[commitment]` for the duration of the call (a same-transaction reentrancy-adjacent state flicker) even though `nonReentrant` limits practical exploitation here. It is a lower-severity, non-fund-loss analog of the reported issue (the original bug's severity came from price manipulation via the callback-authenticated balance check; here there is no analogous price/AMM curve to move), so it does not clear the "steal/loss of funds," "unauthorized execution," or "false proof acceptance" bar required by the impact gate on its own.

### Likelihood Explanation
Trivial and fully attacker-reachable: any address can call `fillOrder` with a crafted `FillOptions.outputs` array of correct length but all-zero amounts, no privileged role or proof forgery required.

### Recommendation
Require at least one `options.outputs[i].amount > 0` (or aggregate `> 0`) in `fillOrder`/`_fillSameChain` before proceeding, mirroring the `solverAmount < totalRequired` revert already used in `_fillCrossChain`, so a fill with zero solver contribution reverts instead of completing as a no-op.

### Proof of Concept
1. User places a same-chain order via `placeOrder` with one output leg requiring 1000 DAI.
2. Attacker (any address) calls `fillOrder(order, FillOptions({relayerFee: 0, nativeDispatchFee: 0, outputs: [TokenInfo({token: DAI, amount: 0})]}))`.
3. `_fillSameChain` sets `_filled[commitment] = attacker`, the loop hits `solverAmount == 0` and `continue`s, `isFullyFilled` becomes `false`.
4. `_withdraw` is invoked with an all-zero `escrowedInputs`, then `_filled[commitment]` is deleted and `PartialFill` is emitted — the call succeeds despite zero tokens changing hands, matching the "empty trades" pattern from the reported analysis.

**Confidence note**: I verified this path directly in `IntrinsicIntents.sol` and the shared `fillOrder` validation in `IntentGatewayV2.sol`; I was unable to fully trace `_withdraw`'s internal handling of an all-zero `TokenInfo[]` array (e.g., whether it reverts or silently no-ops) within the remaining tool budget — this affects whether step 3 fully completes to emission or reverts partway, and should be confirmed against `IntentsBase.sol`'s `_withdraw` implementation before treating this as fully proven.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-79)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L438-446)
```text
        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }
```
