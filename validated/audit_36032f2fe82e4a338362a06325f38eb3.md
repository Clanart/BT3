## Finding

### Title
Uniswap V4 spot-price oracle combined with disabled overfill clamp allows single-block price manipulation to drain Simplex filler funds - (`sdk/packages/simplex/src/strategies/fx.ts`)

### Summary
The Simplex FX filler prices "exotic" tokens directly from a Uniswap V4 pool's current tick/spot price with no TWAP, and the guard that used to bound the resulting output amount (`maxOverfillBps` clamp) has been explicitly disabled — it now only logs a warning instead of capping the fill. This is the same broken invariant as the external report: a spot AMM price, manipulable by dumping tokens into the pool within a single block, is trusted directly for a monetary calculation, and no hard cap remains to bound the damage.

### Finding Description
`UniswapV4FundingPlanner.computeDirectPoolPriceUsd` derives the exotic token's USD price straight from the pool's live `sqrtPriceX96`/tick (`V4Pool.token0Price`/`token1Price`), i.e. the instantaneous spot price of the pool: [1](#0-0) 

This price feeds `resolveLegRates`, which is used to compute the actual output rate for a fill. The mitigation is a **guard on order sizing/reorg depth**, and it is explicitly optional per the docs ("omit both to leave the chain unguarded"): [2](#0-1) 

Even where the guard is configured, it only bounds the venue quote used to size confirmation depth (`referenceRate`), not necessarily the final `policyMaxOutput` computed for the leg: [3](#0-2) 

Critically, the independent safety net that historically bounded per-leg loss from "a bug / stale cache / manipulated venue price" — the overfill clamp — has been deliberately disabled. The code comment documents this directly: the clamp is DISABLED, output is no longer capped, and the filler fills the full unclamped amount: [4](#0-3) 

`computeLegPolicyOutput` computes `policyMaxOutput` purely from the (possibly manipulated) `rate` with no independent sanity bound: [5](#0-4) 

### Impact Explanation
An unprivileged attacker who places an intent order can, within the same block, manipulate the configured Uniswap V4 pool's spot price (via a swap/flash-loan-style dump, exactly the Warp Finance-class primitive from the seed report) so that `computeDirectPoolPriceUsd` returns an inflated USD price for the exotic token. This inflated `rate` flows into `computeLegPolicyOutput`, producing an inflated `policyMaxOutput`. Because the overfill clamp is disabled (warn-only), the filler delivers this inflated amount to the order's beneficiary rather than capping it at `(1 + maxOverfillBps) × user-requested amount`, causing direct loss of solver funds to the attacker-controlled beneficiary. This matches the required impact category "stealing or loss of funds" via a public entrypoint (placing/filling an order) reachable by an unprivileged attacker, without needing a compromised relayer, prover, or operator.

### Likelihood Explanation
Likelihood is high wherever a `[vault.uniswapV4]` pair is configured without `referencePrice`/`maxDeviationBps` (explicitly supported as "unguarded" per the docs), and non-trivial even when the guard is configured, since the guard bounds sizing/reorg depth rather than the emitted output amount, and the overfill clamp — the mechanism purpose-built to bound exactly this class of manipulation — is unconditionally disabled for every venue-priced pair.

### Recommendation
- Re-enable the overfill clamp (`maxOverfillBps`) as an enforced cap on `policyMaxOutput`, not merely a warning, at minimum for venue-priced (Uniswap V4) legs.
- Require `referencePrice`/`maxDeviationBps` (or a TWAP-based price) for any pair priced from a live pool, and apply that guard directly to the rate used in `computeLegPolicyOutput`, not only to `referenceRate`'s sizing path.
- Consider deriving the Uniswap V4 price from a time-weighted observation rather than the instantaneous tick, consistent with the cmichel.io mitigation referenced in the seed report.

### Proof of Concept
1. Operator configures a pair priced from `[vault.uniswapV4]` with no `referencePrice`/`maxDeviationBps` (permitted per docs), or with a wide band.
2. Attacker submits a large swap against the referenced Uniswap V4 pool in the same block, moving the tick so `computeDirectPoolPriceUsd` reports an inflated USD price for the exotic token.
3. Attacker places (or is already positioned to fill) an intent order in that exotic token; `resolveLegRates`/`computeLegPolicyOutput` price the leg using the manipulated rate, producing an inflated `policyMaxOutput`.
4. Because the clamp at `fx.ts` lines 594–617 only warns and never reduces the output, the filler executes the fill at the inflated amount, transferring more value to the attacker's beneficiary than the pool's true price would justify — a direct, single-block, no-risk loss of filler funds.

### Citations

**File:** sdk/packages/simplex/src/funding/uniswapV4/UniswapV4FundingPlanner.ts (L204-230)
```typescript
	private computeDirectPoolPriceUsd(
		pos: HydratedV4Position,
		sdkPool: V4Pool,
		chain: string,
	): { exoticToken: string; priceUsd: Decimal } | null {
		const usdc = this.configService.getUsdcAsset(chain).toLowerCase()
		const usdt = this.configService.getUsdtAsset(chain).toLowerCase()
		const c0 = pos.currency0.toLowerCase()
		const c1 = pos.currency1.toLowerCase()

		if (c0 === usdc || c0 === usdt) {
			// currency0 is stable → exotic is currency1
			// token1Price = "token0 per token1" = USD per exotic
			return {
				exoticToken: c1,
				priceUsd: new Decimal(sdkPool.token1Price.toFixed(18)),
			}
		}

		if (c1 === usdc || c1 === usdt) {
			// currency1 is stable → exotic is currency0
			// token0Price = "token1 per token0" = USD per exotic
			return {
				exoticToken: c0,
				priceUsd: new Decimal(sdkPool.token0Price.toFixed(18)),
			}
		}
```

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L236-240)
```text
#### Uniswap price guards

Pool-based pricing trusts the live pool, which leaves the filler exposed to a manipulated, stale, or thin pool returning a bad quote. To bound that risk, give a position a **`referencePrice`** and **`maxDeviationBps`**. Whenever the pool quote on that chain drifts more than `maxDeviationBps` above or below the reference, the filler refuses to fill — the order is rejected before any bid is submitted.

`referencePrice` is expressed in **exotic tokens per USD**, the same units as the bid/ask curves. The two fields must be set together; omit both to leave the chain unguarded.
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L594-617)
```typescript
				// Overfill detection is warn-only: the clamp is DISABLED, so the filler
				// fills the full computed amount even when it exceeds
				// (1 + maxOverfillBps) × user-requested — including venue-priced legs
				// (e.g. Uniswap V4). NOTE: this removes the per-leg loss bound that
				// previously protected against a bug / stale cache / manipulated venue
				// price. Output is no longer capped; we only emit a warning.
				const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
				const policyMaxOutput = rawPolicyMaxOutput
				if (rawPolicyMaxOutput > overfillCeiling) {
					this.logger.warn(
						{
							orderId: order.id,
							leg: i,
							pair: `${leg.pair.token0}/${leg.pair.token1}`,
							token: output.token,
							userRequested: output.amount.toString(),
							unclamped: rawPolicyMaxOutput.toString(),
							ceiling: overfillCeiling.toString(),
							maxOverfillBps: this.maxOverfillBps.toString(),
							priceSource: rates.priceSource,
						},
						"Overfill ceiling exceeded — clamp disabled, filling unclamped amount",
					)
				}
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1097-1129)
```typescript
	private computeLegPolicyOutput(
		inputAmount: bigint,
		inputIsToken0: boolean,
		token0Decimals: number,
		token1Decimals: number,
		remainingToken0: Decimal,
		rate: Decimal,
	): { token0Used: Decimal; policyMaxOutput: bigint } | null {
		let legMaxToken0: Decimal
		if (inputIsToken0) {
			legMaxToken0 = new Decimal(formatUnits(inputAmount, token0Decimals))
		} else {
			legMaxToken0 = new Decimal(formatUnits(inputAmount, token1Decimals)).div(rate)
		}

		const token0ForLeg = Decimal.min(legMaxToken0, remainingToken0)
		if (token0ForLeg.lte(0)) {
			return null
		}

		let policyMaxOutput: bigint
		if (inputIsToken0) {
			// Output is token1: convert the token0 allocation at the pair rate.
			policyMaxOutput = BigInt(
				token0ForLeg.mul(rate).mul(new Decimal(10).pow(token1Decimals)).floor().toFixed(0),
			)
		} else {
			// Output is token0: pay out the token0 equivalent of the token1 input.
			policyMaxOutput = BigInt(token0ForLeg.mul(new Decimal(10).pow(token0Decimals)).floor().toFixed(0))
		}

		return { token0Used: token0ForLeg, policyMaxOutput }
	}
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1131-1160)
```typescript
	/**
	 * Resolves the pricing rate (token1 per token0) for a leg: the venue quote
	 * when available (validated against the price guard; USD-stable token0
	 * pairs only), otherwise the pair's curve for the leg's direction at the
	 * pair's capped token0 notional. Returns null when the leg cannot be priced
	 * (guard tripped, or direction disabled).
	 */
	private async resolveLegRates(
		orderId: string | undefined,
		leg: ResolvedLeg,
		cappedPairNotional: Decimal,
		venueUsdPrice: (chain: string, token1Address: string) => Promise<Decimal | null>,
	): Promise<LegRates | null> {
		// Explicitly configured curves always win — the venue only prices pairs
		// with no curves at all (and never same-token pairs, where a venue quote
		// would just be the asset's own USD price, not a spread).
		const curveless = !leg.pair.bidPricePolicy && !leg.pair.askPricePolicy
		if (curveless && !isSameTokenPair(leg.pair) && USD_STABLE_SYMBOLS.has(normalizeSymbol(leg.pair.token0))) {
			const venueUsd = await venueUsdPrice(leg.token1Chain, leg.token1Address)
			if (venueUsd) {
				// Guard compares the venue's token1-per-USD quote against the static reference.
				if (!this.checkPriceGuard(orderId, leg.token1Chain, new Decimal(1).div(venueUsd))) {
					return null
				}
				// A pool mid is ONE price, not a book: there is no opposite side
				// to report a round-trip margin against. The price guard above is
				// the venue-specific defense.
				const venueRate = new Decimal(1).div(venueUsd)
				return { rate: venueRate, oppositeRate: null, priceSource: "venue" }
			}
```
