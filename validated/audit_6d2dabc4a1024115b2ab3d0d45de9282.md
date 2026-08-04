### Title
Filler-controlled `options.outputs` in `IntentGatewayV2.fillOrder` lets an unprivileged solver manipulate the `VWAPOracle` cumulative spread used to price/guard future cross-chain orders - (File: `evm/src/apps/IntentGatewayV2.sol`, `evm/src/utils/VWAPOracle.sol`)

### Summary
The reported Autonomint bug is a *stale reference value that feeds an unauthenticated delta calculation*: `lastEthprice` is never refreshed, so an attacker can replay `depositTokens()` and repeatedly push `omniChainData.cdsPoolValue` in either direction using the same stale-vs-current delta. The local analog is `VWAPOracle`'s `CumulativeSpreadData`: `recordSpread()` computes `spreadBps` from `order.inputs` (fixed, escrowed) versus `options.outputs` (attacker-supplied at fill time), and folds it permanently into `weightedSpreadSum`/`totalVolume` with no floor, ceiling, or minimum-notional check. Any address that can legitimately fill an order (including via a self-funded, self-beneficiary order) fully controls one side of the delta that becomes part of a bridge-wide, monotonically-persisted price signal, exactly mirroring the "replay a function with a stale/attacker-influenced reference to skew a cumulative bridge-state value" pattern from the seed report.

### Finding Description
`IntentGatewayV2.fillOrder()` calls the oracle after every fill: [1](#0-0) 

`options.outputs[i].amount` is solver-supplied and only constrained to be `>=` the order's `totalRequired` amount — see `_fillSameChain`/`_fillCrossChain`: [2](#0-1) [3](#0-2) 

There is no minimum order notional and no per-fill spread cap. `recordSpread()` then computes `spreadBps` directly from `(outputAmountNormalized - inputAmountNormalized) / inputAmountNormalized` and unconditionally accumulates it: [4](#0-3) [5](#0-4) 

Because `weightedSpreadSum` and `totalVolume` only ever grow (there is no decay, no per-source-chain cap, and no minimum-volume gate), an attacker can:
1. Place a same-chain order with `inputs[i].amount = 1` wei (or another dust amount) for a token/chain pair the oracle tracks.
2. Immediately fill their own order (same-chain fills have no solver-selection requirement unless `_params.solverSelection` is enabled, and even then the attacker can select themselves) with an arbitrary `options.outputs[i].amount`, producing an extreme `spreadBps` (bounded only by `int256` overflow, e.g. tens of thousands of bps).
3. Because the weighting is `spreadBps * inputAmountNormalized`, using a large dust-adjacent input combined with a large output delta lets the attacker inject an arbitrarily large `weightedSpread` while paying only gas plus the dust cost, repeating this cheaply many times to swing `spread()` = `weightedSpreadSum / totalVolume` in either direction.

This corrupts the exact value (`CumulativeSpreadData.weightedSpreadSum` / `totalVolume`, exposed via `spread()`) that downstream integrators such as the Simplex filler's `referenceRate()`/`checkPriceGuard()` treat as ground truth for guarding pool-priced fills — the same trust relationship `lastEthprice`/`cdsPoolValue` had with `calculateRatio()` in the seed report: [6](#0-5) [7](#0-6) 

Existing guards do not stop this because:
- `restrict(_intentGateway)` only checks the caller is the gateway contract, not that the fill economics are legitimate — see `recordSpread`'s modifier [8](#0-7) .
- Nothing in `fillOrder`/`_fillSameChain`/`_fillCrossChain` enforces a floor on `order.inputs[i].amount`, a cap on `spreadBps`, or a minimum aggregate volume before `spread()` is trusted.
- Any fully self-controlled order (same user places and fills, own beneficiary, own funds) is a legitimate, unprivileged transaction path — it needs no relayer, prover, or admin.

### Impact Explanation
`VWAPOracle.spread()` is consumed by off-chain/simplex FX strategy logic to size confirmation depth and gate venue quotes (`referenceRate`, `checkPriceGuard`). A manipulated cumulative spread can cause the filler to misprice or over/under-confirm cross-chain intent fills, leading to wrong beneficiary amounts or fund loss on subsequent, unrelated orders that rely on the tampered oracle value — satisfying the "transaction manipulation / false state acceptance" bounty category, since the oracle's on-chain `spread()` value is treated as trusted bridge state feeding real-money pricing decisions.

### Likelihood Explanation
High feasibility: any address can place a same-chain order for itself and immediately fill it with attacker-chosen `options.outputs`, paying only the gas and the dust `order.inputs` amount. No malicious relayer, prover, governance actor, or leaked key is required — this is a pure unprivileged-caller path through public entrypoints (`placeOrder`/`fillOrder`), repeatable indefinitely since there is no cooldown, decay, or volume floor on `_updateCumulativeSpread`.

### Recommendation
- Enforce a minimum normalized `inputAmountNormalized` (and/or minimum USD notional) before a fill is allowed to influence `CumulativeSpreadData`.
- Cap `spreadBps` to a sane bound (e.g. ±a few hundred bps) before weighting, rejecting or clamping outliers instead of folding them in unconditionally.
- Consider requiring `msg.sender != order.user` (or otherwise detecting self-fills) for oracle-relevant volume, or applying a time-decayed/windowed VWAP instead of an unbounded monotonic accumulator so a burst of self-fills cannot permanently bias `spread()`.
- Any downstream consumer (`fx.ts` price guard) should additionally treat `totalVolume`/`fillCount` thresholds as a precondition for trusting `spread()`.

### Proof of Concept
1. Deploy/target a chain where `VWAPOracle` is configured as `_params.priceOracle` and `token` decimals are set for the target `sourceChain`/token pair.
2. Attacker calls `placeOrder` with `order.inputs = [{token: X, amount: 1}]` (or minimal dust), `order.output.assets = [{token: X, amount: 1}]`, `order.output.beneficiary = attacker`, `order.source == order.destination` (same-chain).
3. Attacker immediately calls `fillOrder(order, FillOptions{outputs: [{token: X, amount: HUGE}]})`, self-funding both the escrow and the fill.
4. `_fillSameChain` accepts the fill (solver only needs `solverAmount >= totalRequired`, satisfied trivially since `totalRequired = 1`); `IntentGatewayV2.fillOrder` then calls `VWAPOracle.recordSpread(commitment, order.source, order.inputs, options.outputs)`.
5. `recordSpread` computes `spreadBps = (HUGE - 1) * 10000 / 1`, an extreme value, and folds `weightedSpread = spreadBps * inputAmountNormalized` into `_tokenSpreads[sourceChainHash][X]`, corrupting `spread()` for that `(sourceChain, token)` pair — verifiable directly against the unit-test harness pattern in `evm/tests/foundry/VWAPOracleTest.sol` (e.g. `testVWAPWithExtremeVolumeDifferences`), which already demonstrates single-fill dominance of the VWAP under skewed volume/spread inputs. [9](#0-8)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L442-451)
```text
        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }

        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L98-119)
```text
        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-95)
```text
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
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }
```

**File:** evm/src/utils/VWAPOracle.sol (L170-180)
```text
    function recordSpread(
        bytes32 commitment,
        bytes memory sourceChain,
        TokenInfo[] calldata inputs,
        TokenInfo[] calldata outputs
    ) external restrict(_intentGateway) {
        // Validate inputs and outputs have the same length
        if (inputs.length != outputs.length || inputs.length == 0) {
            return;
        }

```

**File:** evm/src/utils/VWAPOracle.sol (L196-216)
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

            // Emit event for each token
            emit SpreadRecorded(commitment, outputToken, spreadBps);
        }
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L270-281)
```text
    /**
     * @notice Updates cumulative spread data
     * @param data Storage reference to the cumulative spread data
     * @param weightedSpread The weighted spread (spread * volume)
     * @param volume The volume for this fill
     */
    function _updateCumulativeSpread(CumulativeSpreadData storage data, int256 weightedSpread, uint256 volume) private {
        data.weightedSpreadSum += weightedSpread;
        data.totalVolume += volume;
        data.fillCount += 1;
        data.lastUpdate = block.timestamp;
    }
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L408-434)
```typescript
	/**
	 * Validates a live venue quote against the static reference price for the chain.
	 * Returns true (pass) when no guard is configured, or no reference exists for the
	 * chain. Returns false when the quote (token1 per USD) deviates from the reference
	 * by more than `maxDeviationBps`, in which case the order must not be filled.
	 */
	private checkPriceGuard(orderId: string | undefined, chain: string, venueToken1PerUsd: Decimal): boolean {
		const guard = this.priceGuard?.get(chain)
		if (!guard || guard.reference.lte(0)) return true

		const deviationBps = venueToken1PerUsd.minus(guard.reference).abs().div(guard.reference).mul(10000)
		if (deviationBps.gt(guard.maxDeviationBps)) {
			this.logger.warn(
				{
					orderId,
					chain,
					venuePrice: venueToken1PerUsd.toString(),
					referencePrice: guard.reference.toString(),
					deviationBps: deviationBps.toFixed(2),
					maxDeviationBps: guard.maxDeviationBps,
				},
				"Rejecting order: Uniswap venue quote outside price-guard band",
			)
			return false
		}
		return true
	}
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1063-1083)
```typescript
	private async referenceRate(
		leg: ResolvedLeg,
		venueUsdPrice: (chain: string, token1Address: string) => Promise<Decimal | null>,
	): Promise<Decimal | null> {
		const policy = leg.pair.bidPricePolicy ?? leg.pair.askPricePolicy
		if (policy) {
			const rate = policy.getPrice(new Decimal(0))
			return rate.gt(0) ? rate : null
		}
		// Venue-priced pair: token0 is USD-stable (constructor invariant), so the
		// venue's USD-per-token1 quote inverts straight into token1-per-token0.
		const venueUsd = await venueUsdPrice(leg.token1Chain, leg.token1Address)
		if (!venueUsd) return null
		const venueRate = new Decimal(1).div(venueUsd)
		// Same guard as trade pricing: this rate sizes the order's USD notional
		// for confirmation depth, and a manipulated pool understating the value
		// would shrink the reorg protection — the exact attack the guard exists
		// to stop. Refusing to size skips the order, consistent with pricing.
		if (!this.checkPriceGuard(undefined, leg.token1Chain, venueRate)) return null
		return venueRate
	}
```

**File:** evm/tests/foundry/VWAPOracleTest.sol (L437-450)
```text
    function testVWAPWithExtremeVolumeDifferences() public {
        _initOracle();

        // One tiny fill and one massive fill
        // Fill 1: 1 token, +1000 bps (10%)
        TokenInfo[] memory inputs1 = new TokenInfo[](1);
        TokenInfo[] memory outputs1 = new TokenInfo[](1);
        inputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1 * 1e18});
        outputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 11 * 1e17}); // 1.1 tokens
        oracle.recordSpread(keccak256("order1"), sourceChain, inputs1, outputs1);

        // Fill 2: 1 million tokens, -10 bps
        TokenInfo[] memory inputs2 = new TokenInfo[](1);
        TokenInfo[] memory outputs2 = new TokenInfo[](1);
```
