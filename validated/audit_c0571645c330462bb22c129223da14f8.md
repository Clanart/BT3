## Local Analog Found

The bug-class here — a deviation/tolerance guard whose bound is set so loosely (or, in this case, entirely disabled) that it stops nothing — reappears almost verbatim in the Simplex filler's FX strategy, which is Hyperbridge's reference solver implementation for the Intent Gateway.

### Title
Overfill protection is explicitly disabled while still being advertised/logged as an active fund-loss bound - ([File: sdk/packages/simplex/src/strategies/fx.ts])

### Summary
`FXFiller.calculateProfitability` computes a `overfillCeiling = (output.amount * (10000n + maxOverfillBps)) / 10000n` — the exact same "compute expected value, apply a bps tolerance band, and check against it" pattern as the reported Chainlink `MAX_PRICE_DEVIATION_FROM_PREVIOUS_ROUND` check. But unlike the reported bug (a bound that is merely too wide), here the comparison result is never enforced: the code computes the ceiling, logs a warning when it's exceeded, and then **fills the full unclamped amount regardless**. [1](#0-0) 

### Finding Description
The comment block directly states the security intent and its removal:

> "Overfill detection is warn-only: the clamp is DISABLED, so the filler fills the full computed amount even when it exceeds (1 + maxOverfillBps) × user-requested — including venue-priced legs (e.g. Uniswap V4). NOTE: this removes the per-leg loss bound that previously protected against a bug / stale cache / manipulated venue price. Output is no longer capped; we only emit a warning." [1](#0-0) 

The output amount for a pool-priced leg is derived from a live Uniswap V4 quote (`venueUsdPrice` → `resolveLegRates`) and is only sanity-checked against a **static reference price** if the operator has opted into `checkPriceGuard` (guarded by `referencePrice`/`maxDeviationBps`, which are optional per the config docs). [2](#0-1) [3](#0-2) 

Even when the price guard *is* configured, the second, independent safety net — the per-order overfill ceiling meant to bound the blast radius of "a bug, stale cache, or manipulated venue" (per the docs) — has been disabled in code while its configuration surface (`maxOverfillBps`, `maxConsecutiveClamps`) and documentation still describe it as an active cap: [4](#0-3) 

The `computeLegPolicyOutput`/`resolveLegRates` pipeline then sizes actual wallet/vault withdrawals (`fundingVenues`, ERC-7821 batch calls) to exactly this unclamped amount before the fill transaction is submitted on-chain via `IntentGatewayV2.fillOrder`. [5](#0-4) 

### Impact Explanation
An attacker who can move a thin/manipulable Uniswap V4 pool used for venue pricing (e.g. a low-liquidity exotic/stable pool, briefly skewed within one block) can place or induce an order that causes the filler to size its output using the manipulated quote. Because the overfill clamp that was designed specifically to bound "manipulated venue price" damage is a no-op, there is no on-path enforcement stopping the filler from delivering an output far in excess of `output.amount` for the escrowed input the attacker/user provided — draining filler-held funds (wallet balance and funding-venue/vault liquidity) in a single `fillOrder` call. This is exactly the "logic attack" / fund-loss class the bounty scope targets: a corrupted/manipulated price feeding directly into an unbounded on-chain transfer, with the one guard built for this exact scenario disabled.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires (a) venue pricing configured without curves (documented supported mode), (b) a manipulable/thin pool for the exotic asset, and (c) the `referencePrice`/`maxDeviationBps` guard either unconfigured or itself set loosely (mirroring the very "too-wide-band" issue from the original report). Given the guard is optional and the overfill clamp is unconditionally disabled, any operator relying on the documented "5% ceiling" protection is not actually protected.

### Recommendation
Re-enable enforcement of the overfill ceiling (clamp `policyMaxOutput`/`rawPolicyMaxOutput` to `overfillCeiling` rather than only logging), or remove/clearly relabel the config/docs so operators do not believe a bound is active when it is not. Additionally, make the Uniswap V4 `referencePrice`/`maxDeviationBps` guard mandatory (not optional) whenever venue-based pricing is used without static curves, matching the recommendation in the source report to prefer conservative, enforced deviation bounds over advisory ones.

### Proof of Concept
1. Configure Simplex with a curve-less pair priced via `[vault.uniswapV4]` (pool-based pricing) and no `referencePrice`/`maxDeviationBps` guard (both optional). [6](#0-5) 
2. An attacker skews the thin exotic/USD Uniswap V4 pool briefly (e.g., via a swap in the same block/tx flow) so `venueUsdPrice` returns an inflated quote for the exotic token in USD terms.
3. Attacker places an Intent Gateway order requesting a small `output.amount` of the exotic token for a modest escrowed input.
4. `resolveLegRates`/`computeLegPolicyOutput` size the fill using the skewed venue rate; `rawPolicyMaxOutput` far exceeds `overfillCeiling`, but the code path at `fx.ts:600-617` only warns and proceeds with the unclamped `finalOutputAmount`. [7](#0-6) 
5. The filler executes `fillOrder` on-chain, delivering far more output tokens than the order specifies, for the attacker's escrowed input — a direct, unbounded fund loss with the documented protective bound never actually applied.

### Citations

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

**File:** sdk/packages/simplex/src/strategies/fx.ts (L619-648)
```typescript
				// Spend the free wallet balance first, down to the configured minBalance
				// reserve — kept liquid for the gas/paymaster pull during
				// validatePaymasterUserOp — then source any remaining shortfall from the
				// funding venues (the vault).
				const tokenAddress = bytes32ToBytes20(output.token).toLowerCase()
				const balance = await this.getAndCacheBalance(tokenAddress, walletAddress, destClient, balanceCache)

				let reserve = 0n
				for (const venue of this.fundingVenues) {
					reserve += venue.walletReserveForToken(destChain, tokenAddress)
				}
				const usableWallet = balance > reserve ? balance - reserve : 0n

				const walletContribution = policyMaxOutput < usableWallet ? policyMaxOutput : usableWallet

				let credited = 0n
				let needed = policyMaxOutput - walletContribution
				for (const venue of this.fundingVenues) {
					if (needed <= 0n) break
					const planned = await venue.planWithdrawalForToken(destChain, walletAddress, tokenAddress, needed, deadlineTimestamp)
					if (planned.calls.length > 0) {
						fundingCalls.push(...planned.calls)
						credited += planned.credited
						needed -= planned.credited
					}
				}

				const effectiveBalance = walletContribution + credited

				const finalOutputAmount = effectiveBalance > policyMaxOutput ? policyMaxOutput : effectiveBalance
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1131-1161)
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
		}
```

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L335-345)
```text
### Overfill Protection

Bounds per-leg loss if the filler's internal pricing is wrong (bug, stale cache, manipulated venue). For every order, the filler clamps its computed output to at most `(1 + maxOverfillBps) × user-requested amount` — this ceiling applies to every strategy. After `maxConsecutiveClamps` consecutive orders where the clamp activated, the **HyperFX** strategy halts itself — a pattern that strongly suggests a systemic pricing error — and requires an operator restart. HyperFX additionally rejects any order where the total output USD value ≥ total input USD value.

Defaults are sensible (`maxOverfillBps = 500` ≈ 5% ceiling, `maxConsecutiveClamps = 3`). Override under `[simplex.overfillProtection]`:

```toml lineNumbers
[simplex.overfillProtection]
maxOverfillBps       = 500   # 5% ceiling above user-requested output
maxConsecutiveClamps = 3     # halt threshold (HyperFX)
```
```

**File:** sdk/packages/simplex/src/config/filler-toml.ts (L342-353)
```typescript
	// Per-position price guard: referencePrice and maxDeviationBps are optional but
	// must be set together. A given chain may not carry conflicting guard values.
	const guardByChain: Record<string, { referencePrice: string; maxDeviationBps: number }> = {}
	for (const position of uniswapV4?.positions ?? []) {
		const hasRef = position.referencePrice !== undefined
		const hasBps = position.maxDeviationBps !== undefined
		if (hasRef !== hasBps) {
			throw new Error(
				"vault.uniswapV4: a position price guard needs both 'referencePrice' and 'maxDeviationBps', or neither",
			)
		}
		if (!hasRef) continue
```
