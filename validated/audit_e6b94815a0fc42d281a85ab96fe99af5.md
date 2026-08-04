Based on the report's core bug — an oracle whose stored average can be permissionlessly steered because the state feeding it is fully attacker-controllable and the weighting math doesn't actually bound manipulation — the closest verified local analog is in `VWAPOracle.sol`, Hyperbridge's cumulative volume-weighted spread accumulator for the Intent Gateway.

### Title
VWAPOracle's weighted-spread formula lets a self-filler inject unbounded spread with negligible volume, corrupting the cumulative average - (File: evm/src/utils/VWAPOracle.sol)

### Summary
`VWAPOracle.recordSpread()` is gated to `_intentGateway` [1](#0-0) , but `IntentGatewayV2` lets any address both create and fill its own order — nothing prevents `maker == filler == recipient`. Because the weighting math computes `weightedSpread = spreadBps * inputAmountNormalized`, and `spreadBps = (outputAmt - inputAmt) * 10000 / inputAmt`, the `inputAmt` term cancels algebraically:

`weightedSpread = (outputAmt - inputAmt) * 10000`

This is independent of the escrowed `inputAmt` that gets added to `totalVolume`. An attacker can therefore pick a vanishingly small `inputAmt` (e.g. 1 wei) while self-delivering an arbitrarily large `outputAmt` to themselves (no net economic loss — same-token self-fill, they receive both the escrow payout and the output they paid), inflating `weightedSpreadSum` by a huge, freely chosen amount while `totalVolume` grows only by 1 wei.

### Finding Description
`_updateCumulativeSpread` blindly accumulates `weightedSpreadSum += weightedSpread` and `totalVolume += volume` [2](#0-1) , and `spread()` returns `weightedSpreadSum / totalVolume` with no floor on volume, no per-fill cap, and no minimum order size [3](#0-2) . Same-token same-chain orders are exactly the case this oracle tracks (`@dev Only supports same-token swaps`) [4](#0-3) , and `recordSpread` computes `spreadBps` and the volume-weighted term purely from the `inputs`/`outputs` amounts passed in by the gateway at fill time [5](#0-4) , with no restriction that the order's escrow size be economically meaningful relative to the claimed output.

This mirrors the Balancer bug class exactly: a value meant to represent a trustworthy time/volume-weighted market signal is derived directly from spot state (per-fill amounts) that an unprivileged, permissionless caller (any order creator/filler pair, including a single self-dealing address) can set to whatever they want, and the accumulator has no resistance to being steered by repeated cheap calls — just as `updatePrice()` in the Balancer report let anyone push the stored TWAP toward a manipulated spot regime.

### Impact Explanation
Any address can drive `VWAPOracle.spread()` to an arbitrary value (extreme positive or negative, effectively unbounded since only `int256` overflow limits it) by submitting a sequence of cheap same-token self-fills with minimal escrow and large self-paid outputs. Since `spread()` is the exposed, canonical signal (`IIntentPriceOracle`) meant to represent a volume-weighted average of real filler behavior for a `(sourceChain, token)` pair, any consumer that gates decisions on this value — reputation/solver-selection logic, governance dashboards, or automated pricing/guardrail logic that treats "spread" as a manipulation-resistant, volume-real signal — receives false state. This is a logic/false-state-acceptance defect in a component whose entire purpose is manipulation resistance.

### Likelihood Explanation
High. The only precondition is calling `IntentGatewayV2`'s public order-creation and fill entry points against oneself with a same-token pair — no relayer, prover, admin, or governance actor is required, and the attacker never needs external counterparties since it is a fully self-dealing loop (escrow returns to the same address as filler payout, output is self-delivered). Gas cost is the only expense; the accumulator can be moved to an extreme value with a handful of transactions.

### Recommendation
Do not let `weightedSpread` cancel the volume term: weight by the input amount without letting it divide out of the numerator/denominator symmetrically, or independently enforce a minimum economically meaningful order size (e.g., a floor on `inputAmountNormalized`) before a fill is allowed to update the accumulator. Consider capping the maximum `|spreadBps|` accepted per fill and/or requiring `fillCount`/`totalVolume` to exceed a minimum threshold before `spread()` is trusted by downstream consumers, analogous to not trusting a TWAP snapshot built from adversarially-sized samples.

### Proof of Concept
1. Attacker calls `IntentGatewayV2` to create a same-chain, same-token order with `maker = recipient = attacker`, escrowing `inputAmt = 1` (smallest unit) of token `T`.
2. Attacker (as filler) fills the order, delivering `outputAmt = 1_000_000e18` of `T` to themselves.
3. `IntentGatewayV2` invokes `VWAPOracle.recordSpread(commitment, sourceChain, inputs, outputs)`.
4. Inside `recordSpread`: `spreadBps = (1_000_000e18 - 1) * 10000 / 1 ≈ 1e22` (effectively unbounded), `weightedSpread = spreadBps * 1 = (1_000_000e18-1)*10000`.
5. `_updateCumulativeSpread` adds this huge `weightedSpread` to `weightedSpreadSum` while `totalVolume` increases by only `1`.
6. `spread(sourceChain, T)` now returns `weightedSpreadSum / totalVolume`, an astronomically skewed figure, after the attacker spent only gas and moved tokens between accounts they control — no real market volume backs the recorded spread. Repeating this with an opposite-signed extreme in the next call lets the attacker toggle the reported spread to any target value at will.

Note: I was not able to confirm within this index which on-chain or off-chain component (e.g., solver reputation, Simplex pricing/guardrails) currently consumes `VWAPOracle.spread()` as a trust input — that binding should be verified in a live session to fully scope downstream fund-impact, since the docs (`sdk/packages/simplex`) reference separate Uniswap-pool-based price guards (`referencePrice`/`maxDeviationBps`) that did not show a direct call into `VWAPOracle` in the code I could search.

### Citations

**File:** evm/src/utils/VWAPOracle.sol (L28-30)
```text
 * @notice Gas-efficient oracle for tracking cumulative VWAP spreads for same-token swaps
 * @dev Only supports same-token swaps
 * @dev Tracks spreads per (source chain, token address)
```

**File:** evm/src/utils/VWAPOracle.sol (L141-147)
```text
    function spread(bytes memory sourceChain, address token) external view returns (int256) {
        bytes32 chainHash = keccak256(sourceChain);
        CumulativeSpreadData memory data = _tokenSpreads[chainHash][token];
        if (data.totalVolume == 0) return 0;

        return data.weightedSpreadSum / int256(data.totalVolume);
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L170-175)
```text
    function recordSpread(
        bytes32 commitment,
        bytes memory sourceChain,
        TokenInfo[] calldata inputs,
        TokenInfo[] calldata outputs
    ) external restrict(_intentGateway) {
```

**File:** evm/src/utils/VWAPOracle.sol (L196-211)
```text
            // Normalize both amounts to 18 decimals for comparison
            uint256 inputAmountNormalized = _normalizeAmount(inputs[i].amount, inputDecimals);
            uint256 outputAmountNormalized = _normalizeAmount(outputs[i].amount, outputDecimals);

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
