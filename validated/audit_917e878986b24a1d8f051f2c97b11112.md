### Title
FXFiller pays out uncapped, venue-price-derived output amounts after the overfill clamp was disabled, allowing pool-price manipulation to drain filler funds - ([File: sdk/packages/simplex/src/strategies/fx.ts])

### Summary
The External Report's core broken invariant is: a decision that moves funds (liquidation) is driven by a price value that can be pushed to an outlier ("bogus") reading, with no bound tying the executed value to a sane/verified reference. In Hyperbridge, `SimplexPaymaster.sol` already implements the recommended mitigations (non-zero and staleness checks on every Chainlink read via `_getOraclePrice`) [1](#0-0) . The real local analog of the *un-mitigated* bug class lives in the Simplex filler's FX strategy, where a Uniswap V4 pool spot price feeds directly into the amount the filler pays out on `fillOrder`, and the one safety clamp that used to bound the blast radius of a bad/manipulated quote has been explicitly disabled.

### Finding Description
`resolveLegRates` in `FXFiller` prices "venue" legs (pairs with no configured curves) straight from a live Uniswap V4 pool quote (`venueUsdPrice`), inverted into a `rate`, and only rejects it if an optional `priceGuard` is configured and tripped [2](#0-1) . The guard is opt-in: the docs and code both note it can be left unconfigured ("omit both to leave the chain unguarded") [3](#0-2) [4](#0-3) .

That `rate` feeds `computeLegPolicyOutput`, which converts the leg's input amount directly into `policyMaxOutput` — the token amount the filler will hand to the user via `fillOrder` [5](#0-4) .

Previously, an `overfillCeiling` (`output.amount * (1 + maxOverfillBps)/10000`) capped `policyMaxOutput` to bound loss from "a bug / stale cache / manipulated venue price." This clamp is now dead code — the comment in the source states it explicitly: "the clamp is DISABLED... This removes the per-leg loss bound that previously protected against a bug / stale cache / manipulated venue price. Output is no longer capped; we only emit a warning." [6](#0-5) . `rawPolicyMaxOutput` is used verbatim as `policyMaxOutput`, unclamped, and is what ultimately becomes `finalOutputAmount` pushed into `fillerOutputs` and paid out on-chain [7](#0-6) .

The corrupted value is the venue-derived `rate` (ultimately `venueUsd`/`venueRate` from a single Uniswap V4 pool tick read by `UniswapV4LiquidityState.refresh`/`computeDirectPoolPriceUsd`) [8](#0-7) [9](#0-8) . A single-block spot-price read of a thin pool is exactly the "price outbreak" primitive in the original report — no TWAP, no delay, and (when `priceGuard` is unconfigured, or configured with a wide `maxDeviationBps`) no deviation check at all before the number drives an irreversible on-chain transfer.

### Impact Explanation
An attacker who can move the tick of the exotic-token/USDC(T) Uniswap V4 pool the filler is quoting from (e.g., via a large swap or flash-loan-funded trade against a thin pool, within one block of order submission) can make `venueUsd` read far below the true market price. That inflates the inverted `rate` (token1 per token0), inflating `policyMaxOutput`. With the overfill clamp disabled, the filler will pay out that inflated, unclamped amount on `fillOrder`, directly transferring the filler's escrowed/vault assets to the attacker's order at a non-market rate — the same "obtain more value than a healthy market allows, at the counterparty's expense" outcome as the liquidation-on-bogus-price bug. This is a direct fund-loss/logic-attack vector against the filler's on-chain balances, matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
Likelihood is meaningful but conditioned on operator configuration: it requires (a) a venue-priced pair with no bid/ask curves, sourced from a Uniswap V4 position, and (b) the `priceGuard` either unset or configured with a loose `maxDeviationBps`. Both are supported, documented configurations ("the recommended approach — let the pool act as your price oracle" and "omit both to leave the chain unguarded") [10](#0-9) , and the explicit in-code comment confirms this exact attack ("manipulated venue price") was a known, previously-mitigated risk that was deliberately reverted to warn-only. Any unprivileged attacker with capital to move the target pool's tick for one call (no relayer, prover, or governance compromise required) can trigger it.

### Recommendation
Re-enable the overfill ceiling as a hard cap (not warn-only) on `policyMaxOutput` for venue-priced legs, or reject the leg entirely when `rawPolicyMaxOutput > overfillCeiling` instead of filling the unclamped amount. Separately, make the Uniswap V4 price guard mandatory (not optional) for any pair with no configured curves, and prefer a short-window TWAP over a single-block spot tick, mirroring the freshness/anti-outlier mitigations already applied to `SimplexPaymaster`'s Chainlink reads (`_getOraclePrice`'s staleness/non-positive checks) [1](#0-0) .

### Proof of Concept
1. Configure `FXFiller` with a venue-priced pair backed by a Uniswap V4 position (`[vault.uniswapV4]`), no `referencePrice`/`maxDeviationBps` set (unguarded), per supported config [11](#0-10) .
2. Attacker swaps a large amount into the pool in the same block/near the filler's quote read, shifting the tick so `computeDirectPoolPriceUsd`/`getExoticTokenPrice` returns a materially understated USD price for the exotic token [9](#0-8) .
3. Attacker places an IntentGateway order whose leg resolves to this pair; `resolveLegRates` computes an inflated `rate` from the manipulated quote, unopposed by the (unconfigured) `checkPriceGuard` [12](#0-11) .
4. `computeLegPolicyOutput` converts this into an inflated `policyMaxOutput`; the overfill clamp check only logs a warning and does not reduce the amount [6](#0-5) .
5. The filler signs/executes `fillOrder` with `finalOutputAmount == policyMaxOutput`, transferring the inflated amount of tokens to the attacker's order, realizing the loss.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L426-442)
```text
    /// @dev Fetch a Chainlink price normalized to 8 decimals.
    ///      Reverts on stale or non-positive answers.
    function _getOraclePrice(AggregatorV3Interface oracle, uint8 oracleDecimals) internal view returns (uint256) {
        (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();

        if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
        if (block.timestamp - updatedAt > maxOracleAge) {
            revert StaleOraclePrice(address(oracle), updatedAt);
        }

        if (oracleDecimals < 8) {
            return uint256(answer) * (10 ** (8 - oracleDecimals));
        } else if (oracleDecimals > 8) {
            return uint256(answer) / (10 ** (oracleDecimals - 8));
        }
        return uint256(answer);
    }
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L414-434)
```typescript
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

**File:** sdk/packages/simplex/src/strategies/fx.ts (L632-713)
```typescript
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

				if (finalOutputAmount === 0n) {
					this.logger.info(
						{
							orderId: order.id,
							pair: `${leg.pair.token0}/${leg.pair.token1}`,
							token: output.token,
							inputAmount: input.amount.toString(),
							fillerBalance: balance.toString(),
						},
						"Skipping leg: no available balance for required output token",
					)
					// Aligned zero output (see budget-exhausted case above).
					fillerOutputs.push({ token: output.token, amount: 0n })
					fillerOutputLegs.push(i)
					continue
				}

				if (policyMaxOutput < output.amount) {
					// Name the actual limiter: a fair order capped by maxOrderSize
					// reads very differently from one demanding a better-than-book
					// rate, and the two have different operator fixes.
					const capLimited = token0Used.lt(legNotionals[i])
					this.logger.info(
						{
							orderId: order.id,
							pair: `${leg.pair.token0}/${leg.pair.token1}`,
							token: output.token,
							inputAmount: input.amount.toString(),
							legNotional: legNotionals[i].toString(),
							pricedNotional: token0Used.toString(),
							maxOrderSize: leg.pair.maxOrderSize.toString(),
							policyOutput: policyMaxOutput.toString(),
							userRequested: output.amount.toString(),
							limiter: capLimited ? "maxOrderSize" : "price",
						},
						capLimited
							? "Skipping order: maxOrderSize caps the leg below the user's requested amount"
							: "Skipping order: filler price yields less than user's requested amount",
					)
					return 0
				}

				if (sourceChain !== destChain && finalOutputAmount < output.amount) {
					this.logger.info(
						{
							orderId: order.id,
							pair: `${leg.pair.token0}/${leg.pair.token1}`,
							token: output.token,
							inputAmount: input.amount.toString(),
							fillerBalance: balance.toString(),
							userRequested: output.amount.toString(),
						},
						"Skipping cross-chain order: insufficient balance for full fill",
					)
					return 0
				}

				// Decrement the wallet pool by what this leg drew from it (vault-sourced
				// tokens are tracked by the venue's own reservations) so repeated outputs
				// of the same token share one wallet balance.
				const walletRemaining = balance - walletContribution
				balanceCache.set(tokenAddress, walletRemaining > 0n ? walletRemaining : 0n)

				fillerOutputs.push({ token: output.token, amount: finalOutputAmount })
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

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1138-1161)
```typescript
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

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L208-240)
```text
#### Pool-Based Pricing

When **`[vault.uniswapV4]`** lists at least one position, cross-asset pairs without curves derive bid/ask prices from **Uniswap V4 pool state** (current tick). This is the recommended approach — let the pool act as your price oracle rather than maintaining static curves.

With Uniswap V4 positions configured, you can **omit** `bidPriceCurve` and `askPriceCurve` on the pair. Pool pricing requires the pair's `token0` to be a USD stablecoin, and same-token pairs always need their curve. The optional **`spreadBps`** field (basis points) sets the slippage tolerance for on-chain LP redemptions; defaults to `50` (0.50%).

Uniswap V4 venue pricing uses pools that pair the exotic token with **USDC or USDT** (addresses from your chain config). When multiple positions exist for the same exotic token on a chain, the most-liquid qualifying pool's price is used.

```toml lineNumbers
[assets.CNGN]
"EVM-8453" = "0x46C85152bFe9f96829aA94755D9f915F9B10EF5F"

[[pairs]]
token0 = "USDC"
token1 = "CNGN"
maxOrderSize = "5000"       # no curves — priced from the pool

[vault.uniswapV4]
spreadBps = 50  # 0.5% slippage tolerance on LP redemptions
positions = [
    { chain = "EVM-8453", tokenId = "2087350" },
]
```

<Callout type="info">
Startup validation requires a pricing source per pair: **either** bid/ask price curves, **or** at least one `[vault.uniswapV4]` position. A pair with neither fails validation.
</Callout>

#### Uniswap price guards

Pool-based pricing trusts the live pool, which leaves the filler exposed to a manipulated, stale, or thin pool returning a bad quote. To bound that risk, give a position a **`referencePrice`** and **`maxDeviationBps`**. Whenever the pool quote on that chain drifts more than `maxDeviationBps` above or below the reference, the filler refuses to fill — the order is rejected before any bid is submitted.

`referencePrice` is expressed in **exotic tokens per USD**, the same units as the bid/ask curves. The two fields must be set together; omit both to leave the chain unguarded.
```

**File:** sdk/packages/simplex/src/funding/uniswapV4/UniswapV4LiquidityState.ts (L136-212)
```typescript
	async refresh(): Promise<void> {
		const client = this.clientManager.getPublicClient(this.chain)
		const chainId = chainIdFromIdentifier(this.chain)

		// Group positions by poolId to avoid duplicate pool state fetches
		const poolIds = new Set(this.tokenIdToPoolId.values())

		// Fetch slot0 + liquidity for each unique pool via StateView
		const poolStateMap = new Map<string, { sqrtPriceX96: bigint; tick: number; poolLiquidity: bigint }>()

		for (const poolId of poolIds) {
			const [slot0Result, poolLiquidity] = await Promise.all([
				client.readContract({
					address: this.stateView,
					abi: UNISWAP_V4_STATE_VIEW_ABI,
					functionName: "getSlot0",
					args: [poolId as HexString],
				}) as Promise<[bigint, number, number, number]>,
				client.readContract({
					address: this.stateView,
					abi: UNISWAP_V4_STATE_VIEW_ABI,
					functionName: "getLiquidity",
					args: [poolId as HexString],
				}) as Promise<bigint>,
			])

			poolStateMap.set(poolId, {
				sqrtPriceX96: slot0Result[0],
				tick: slot0Result[1],
				poolLiquidity,
			})
		}

		// Refresh per-position liquidity and rebuild SDK Pool objects
		for (const pos of this.positions.values()) {
			const key = pos.tokenId.toString()
			const poolId = this.tokenIdToPoolId.get(key)
			const poolState = poolId ? poolStateMap.get(poolId) : undefined
			if (!poolId || !poolState) {
				throw new Error(
					`UniswapV4 refresh: missing pool state for tokenId ${key} (poolId=${poolId ?? "undefined"})`,
				)
			}

			// Read current position liquidity
			const liquidity = (await client.readContract({
				address: pos.positionManager,
				abi: UNISWAP_V4_POSITION_MANAGER_ABI,
				functionName: "getPositionLiquidity",
				args: [pos.tokenId],
			})) as bigint

			pos.liquidity = liquidity
			const prevOnChain = this.lastOnChainLiquidity.get(key) ?? liquidity
			const decrease = prevOnChain > liquidity ? prevOnChain - liquidity : 0n
			const prevConsumed = this.consumed.get(key) ?? 0n
			const newConsumed = prevConsumed > decrease ? prevConsumed - decrease : 0n
			this.consumed.set(key, newConsumed)
			this.lastOnChainLiquidity.set(key, liquidity)
			pos.remainingLiquidity = liquidity > newConsumed ? liquidity - newConsumed : 0n
			pos.sqrtPriceX96 = poolState.sqrtPriceX96
			pos.currentTick = poolState.tick

			// Build SDK Pool (without tick data provider — we only need amount calcs)
			const currency0 = currencyFromHydratedDecimals(chainId, pos.currency0, pos.decimals0)
			const currency1 = currencyFromHydratedDecimals(chainId, pos.currency1, pos.decimals1)

			const sdkPool = new V4Pool(
				currency0,
				currency1,
				pos.fee,
				pos.tickSpacing,
				pos.hooks,
				poolState.sqrtPriceX96.toString(),
				poolState.poolLiquidity.toString(),
				poolState.tick,
			)
```

**File:** sdk/packages/simplex/src/funding/uniswapV4/UniswapV4FundingPlanner.ts (L200-233)
```typescript
	/**
	 * Computes the USD price of the non-stable token in a pool.
	 * Returns null if neither currency is USDC/USDT on this chain.
	 */
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

		return null
	}
```
