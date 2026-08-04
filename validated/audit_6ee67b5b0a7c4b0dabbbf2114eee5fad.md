## Analysis

The Nibbl bug's core invariant is: **a cumulative accumulator that is expected to grow indefinitely will eventually overflow, and because Solidity 0.8's checked arithmetic reverts on overflow, the accumulator update function (and everything that calls it) permanently breaks** once that threshold is crossed.

The direct local analog is `VWAPOracle.sol`'s `_updateCumulativeSpread`, which is structurally identical to Nibbl's `_updateTwav`: an unbounded, never-reset accumulator fed by a public-entrypoint-reachable function (`recordSpread`, called unconditionally from `IntentGatewayV2.fillOrder`). [1](#0-0) 

```solidity
function _updateCumulativeSpread(CumulativeSpreadData storage data, int256 weightedSpread, uint256 volume) private {
    data.weightedSpreadSum += weightedSpread;
    data.totalVolume += volume;
    data.fillCount += 1;
    data.lastUpdate = block.timestamp;
}
```

`weightedSpreadSum` and `totalVolume` are never decremented or capped, and this function is called on every fill: [2](#0-1) 

```solidity
if (_params.priceOracle != address(0)) {
    IIntentPriceOracle(_params.priceOracle)
        .recordSpread(commitment, order.source, order.inputs, options.outputs);
}
```

This call is not wrapped in a try/catch — it happens after `_fillSameChain`/`_fillCrossChain` already moved escrow/output tokens in the same transaction, so a revert inside `recordSpread` rolls back the entire fill, including token transfers.

`spreadBps` (and hence `weightedSpread = spreadBps * inputAmountNormalized`) is attacker-influenced because both `inputs` (order-defined by the order creator) and `outputs` (filler-supplied) are external, unchecked inputs, and there is no enforcement that `inputToken == outputToken` despite the contract's `@dev Only supports same-token swaps` comment: [3](#0-2) 

```solidity
int256 spreadBps = 0;
if (inputAmountNormalized > 0) {
    int256 amountDiff = int256(outputAmountNormalized) - int256(inputAmountNormalized);
    spreadBps = (amountDiff * int256(BPS_DENOMINATOR)) / int256(inputAmountNormalized);
}
int256 weightedSpread = spreadBps * int256(inputAmountNormalized);
_updateCumulativeSpread(_tokenSpreads[sourceChainHash][inputToken], weightedSpread, inputAmountNormalized);
```

An attacker who creates and self-fills orders (or deploys their own worthless ERC20 with self-minted supply and attacker-controlled `decimals()`, since `outputDecimals` is read directly from `IERC20Metadata(outputToken).decimals()`) can drive `inputAmountNormalized` tiny and `outputAmountNormalized` arbitrarily large, producing an extreme `spreadBps`. Repeating this (or fewer, larger-magnitude fills) pushes `weightedSpreadSum` toward `int256` bounds. Once the next `+=` overflows, Solidity 0.8's checked arithmetic reverts — permanently breaking `recordSpread` and, transitively, every future `fillOrder` call for that `(sourceChain, token)` pair whenever `_params.priceOracle != address(0)`.

### Title
Unbounded `weightedSpreadSum`/`totalVolume` accumulator in `VWAPOracle` overflows and permanently DoSes `fillOrder` - (File: evm/src/utils/VWAPOracle.sol)

### Summary
`VWAPOracle._updateCumulativeSpread` accumulates `weightedSpreadSum` (`int256`) and `totalVolume` (`uint256`) per `(sourceChain, token)` with no decay, cap, or reset, exactly mirroring the Nibbl `_updateTwav`/`_getTwav` overflow pattern. `IntentGatewayV2.fillOrder` calls this unconditionally and un-guarded (no try/catch) whenever a price oracle is configured.

### Finding Description
`recordSpread` computes `weightedSpread = spreadBps * inputAmountNormalized` from attacker-influenced `order.inputs` and filler-supplied `options.outputs`, then adds it to the storage accumulator forever [3](#0-2) . Because Solidity 0.8.24 arithmetic reverts on overflow rather than wrapping, once `weightedSpreadSum` (or `totalVolume`) crosses its type bound, every subsequent `_updateCumulativeSpread` call for that key reverts.

`fillOrder` calls `recordSpread` directly after moving escrow and fill funds within the same transaction, with no error handling [2](#0-1) , so the revert unwinds the whole fill.

### Impact Explanation
Once the accumulator for a given `(sourceChain, token)` overflows, `fillOrder` becomes permanently unusable for that token pair whenever the price oracle is active — a persistent, self-inflicted logic break that blocks legitimate order settlement and forces affected orders into cancellation/refund paths instead of normal fills. This matches the Hyperbridge impact gate's "transaction manipulation / logic attacks" category: an unprivileged actor corrupts shared on-chain state (`_tokenSpreads[chainHash][token]`) such that the intended settlement path (`fillOrder`) can no longer execute for anyone using that token pair.

### Likelihood Explanation
The attacker needs no privileged role, relayer, prover, or governance access — only the ability to create and fill their own orders (or deploy a throwaway ERC20 to control `decimals()` and mint an arbitrary self-owned balance to inflate `outputAmountNormalized`/`inputAmountNormalized` asymmetry). Repeated self-fills accumulate `weightedSpreadSum` toward the `int256` bound with no mitigating cap in the code.

### Recommendation
Bound or periodically reset `weightedSpreadSum`/`totalVolume` (e.g., windowed accumulation, saturating arithmetic, or `unchecked` blocks paired with wraparound-safe difference math as recommended in the Nibbl fix), and wrap the `recordSpread` call in `fillOrder` so an oracle failure cannot block order settlement.

### Proof of Concept
1. Attacker deploys a throwaway ERC20 `EvilToken` overriding `decimals()` to return a small value (e.g. `1`), and mints itself a large raw balance.
2. Attacker configures `EvilToken` as both `inputToken`/relevant token via `_tokenDecimals` registration path (or uses an already-registered low-decimal token) and repeatedly creates+self-fills orders where `inputs[i].amount` is minimal and `outputs[i].amount` (filler-supplied, attacker-controlled since attacker is also the filler) is large, maximizing `spreadBps` and thus `weightedSpread` per call.
3. Repeating `fillOrder` calls drives `_tokenSpreads[sourceChainHash][inputToken].weightedSpreadSum` toward `type(int256).max`/`min`.
4. The next call to `_updateCumulativeSpread` overflows the checked `int256` addition and reverts, causing that `fillOrder` transaction (including the legitimate token transfers already performed by `_fillSameChain`/`_fillCrossChain`) to revert entirely, and permanently blocking all further fills for that `(sourceChain, token)` key as long as the oracle is set.

### Citations

**File:** evm/src/utils/VWAPOracle.sol (L200-211)
```text
            // Calculate spread for this token: (output - input) / input * 10000
            // Positive spread = filler provided more tokens (good for user)
            // Negative spread = filler provided fewer tokens (filler captured spread)
            int256 spreadBps = 0;
            if (inputAmountNormalized > 0) {
                int256 amountDiff = int256(outputAmountNormalized) - int256(inputAmountNormalized);
                spreadBps = (amountDiff * int256(BPS_DENOMINATOR)) / int256(inputAmountNormalized);
            }

            // Update cumulative spread data for this token (weighted by volume)
            int256 weightedSpread = spreadBps * int256(inputAmountNormalized);
            _updateCumulativeSpread(_tokenSpreads[sourceChainHash][inputToken], weightedSpread, inputAmountNormalized);
```

**File:** evm/src/utils/VWAPOracle.sol (L276-281)
```text
    function _updateCumulativeSpread(CumulativeSpreadData storage data, int256 weightedSpread, uint256 volume) private {
        data.weightedSpreadSum += weightedSpread;
        data.totalVolume += volume;
        data.fillCount += 1;
        data.lastUpdate = block.timestamp;
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L448-451)
```text
        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
```
