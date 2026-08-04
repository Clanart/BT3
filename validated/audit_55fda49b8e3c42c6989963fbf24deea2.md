Confirmed: the `PhantomOrderPriceSnapshot.medianPrice` is used directly by `PhantomSnapshotIntentQuoteStrategy.quoteSnapshot` at [1](#0-0)  to compute the exact `amountIn`/`amountOut` that becomes the on-chain order's `inputs`/`output.assets` when the user places a real, escrow-backed IntentGateway V2 order. That median is a liquidity-weighted median over live `balanceOf`/`maxWithdraw` reads taken at indexer-handler time, as computed in `aggregatePhantomBids` / `weightedMedian` at [2](#0-1)  and [3](#0-2) , so anyone able to inflate their own on-chain token balance for an instant at the moment the snapshot's RPC reads execute can pull the reported price to their own bid.

### Title
Liquidity-weighted Phantom price snapshot can be skewed by a momentary balance inflation, mispricing real on-chain orders - ([File: sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts])

### Summary
The Phantom snapshot mechanism used to price IntentGateway swaps weights each competing solver's quoted price by that solver's live on-chain token balance (`balanceOf` + ERC-4626 `maxWithdraw`) read via RPC at snapshot time. Nothing pins that balance to the moment the bid was placed, to an escrow, or to a sustained holding period. A solver can place a lowball/favorable-to-solver bid and then, in the same or an adjacent block before the snapshot handler executes its RPC reads, temporarily inflate its own measured balance (self-transfer from another controlled address, a flashloan, or a vault deposit/withdraw round-trip) to dominate the weighted median, then immediately reverse it. This is structurally the same "snapshot race" pattern as the reported LiquidityMining issue — a participant waits until just before the value-determining snapshot and moves capital in only for that instant to capture the outcome.

### Finding Description
`aggregatePhantomBids` computes `weight = getTotalSolverBalance(...)` for each verified bid — a live read of the solver's `balanceOf` on the output token plus any configured ERC-4626 vault `maxWithdraw` positions [4](#0-3) . This weight is fed straight into `weightedMedian`, which returns the price of whichever bid accumulates ≥50% of total weight [3](#0-2) . The resulting `medianPrice` is persisted as the authoritative `PhantomOrderPriceSnapshot` [5](#0-4)  and is the exact value `PhantomSnapshotIntentQuoteStrategy.quoteSnapshot` uses to derive the real `amountIn`/`amountOut` for the order a user is about to place on-chain [6](#0-5) . There is no time-lock, escrow, stake-bonding, or historical-average requirement on the measured balance — only an instantaneous RPC read taken once, at handler-execution time, after the bid window closes (`PhantomBidWindowExhausted`). A solver controlling more capital than the amount they intend to actually deliver can borrow/shuffle tokens into their own address for the single block in which the snapshot's `eth_call`s run, guaranteeing their quoted price dominates the median, and withdraw the capital immediately after. The `sortBids`/`selectAndExecuteBest` autopilot and any user relying on `quoteIntent` will then size a real, escrowed order against this manipulated price with no on-chain enforcement that the attacker's balance still exists.

### Impact Explanation
Since `quoteIntent`'s output feeds directly into the `inputs`/`output.assets` of a real IntentGateway V2 order (real escrowed tokens, per the docs at `docs/content/developers/sdk/api/intent-gateway.mdx`), a manipulated median lets an attacker-controlled solver bias the price a user locks into their on-chain order. This causes users to receive materially worse output than a fair liquidity-weighted market price would produce — a direct loss of funds for the counterparties trusting the quote, and a wrong-amount settlement relative to true available liquidity, without needing any relayer, prover, or governance compromise.

### Likelihood Explanation
Any address can be an EIP-7702-delegated "solver" and hold or borrow ERC-20/vault balances; no special privilege is required. The snapshot's RPC reads happen at a deterministic, observable trigger (`PhantomBidWindowExhausted`), so the attacker knows precisely when to hold the inflated balance. The only friction is needing enough capital (own or via flashloan) for one block/RPC-read window, which is materially cheaper than sustained liquidity provision.

### Recommendation
Do not weight the median by an instantaneous balance read taken after the fact. Options: require solvers to bond/escrow the balance used as weight (so it cannot be transient), average the balance over multiple blocks/samples prior to the snapshot trigger, or bind weight to a balance already reserved/locked by an on-chain commitment made before bids close, rather than a value derived from a same-block `eth_call`.

### Proof of Concept
1. Attacker controls two EVM addresses: `Solver` (the delegated bidder) and `Funder`.
2. `Solver` places a phantom bid quoting an output amount favorable to itself (low output) via `place_bid`.
3. Once `PhantomBidWindowExhausted` is imminent/observed off-chain, in the same block `Funder` transfers (or flash-loans) a large balance of the output token / vault shares into `Solver`.
4. The indexer handler `handlePhantomOrderPrices` fires, calling `aggregatePhantomBids`, which reads `Solver`'s balance via `getTotalSolverBalance` at that instant [4](#0-3)  and assigns it as `weight` for `Solver`'s quote.
5. `weightedMedian` selects `Solver`'s unfavorable price as the reported median because its weight dominates [3](#0-2) .
6. Immediately after, `Solver` returns the borrowed/transferred balance to `Funder`.
7. A user calling `quoteIntent` receives `amountOut` computed from this skewed `medianPrice` [7](#0-6)  and places a real on-chain order sized to the manipulated price, losing value relative to the true market rate.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/quote/phantomSnapshot.ts (L83-100)
```typescript
	private quoteSnapshot(
		params: QuoteIntentParams,
		protocolFeeBps: bigint,
		snapshot: PhantomOrderPriceSnapshot,
	): PhantomSnapshotQuoteIntentResult {
		if (params.amountIn !== undefined) {
			const netAmountIn = deductProtocolFee(params.amountIn, protocolFeeBps)
			const amountOut = (netAmountIn * snapshot.medianPrice) / snapshot.standardAmount
			if (amountOut <= 0n) {
				throw new InvalidPhantomSnapshotError(snapshot.commitment, "quote rounds down to zero output")
			}
			return this.buildResult("EXACT_INPUT", params.amountIn, amountOut, protocolFeeBps, snapshot)
		}

		if (params.amountOut === undefined) throw new Error("Quote amount is missing after validation")
		const netAmountIn = divCeil(params.amountOut * snapshot.standardAmount, snapshot.medianPrice)
		const amountIn = grossUpForProtocolFee(netAmountIn, protocolFeeBps)
		return this.buildResult("EXACT_OUTPUT", amountIn, params.amountOut, protocolFeeBps, snapshot)
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L121-136)
```typescript
export function weightedMedian(entries: { price: bigint; weight: bigint }[]): bigint {
	const sorted = [...entries].sort((a, b) => (a.price < b.price ? -1 : a.price > b.price ? 1 : 0))
	const totalWeight = sorted.reduce((acc, e) => (e.weight > 0n ? acc + e.weight : acc), 0n)

	if (totalWeight === 0n) {
		return sorted[Math.floor(sorted.length / 2)].price
	}

	let cumulative = 0n
	for (const entry of sorted) {
		if (entry.weight <= 0n) continue
		cumulative += entry.weight
		if (cumulative * 2n >= totalWeight) return entry.price
	}
	return sorted[sorted.length - 1].price
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L356-370)
```typescript
async function getTotalSolverBalance(
	evmRpcUrl: string,
	chain: string,
	token: string,
	solver: string,
	yieldVaults: YieldVaultMap,
): Promise<bigint> {
	const padded = solver.replace("0x", "").padStart(64, "0")
	const raw = await ethCallUint(evmRpcUrl, token, `0x70a08231${padded}`) // balanceOf(address)
	const vaults = yieldVaults[chain]?.[token.toLowerCase()] ?? []
	const vaultBalances = await Promise.all(
		vaults.map((v) => ethCallUint(evmRpcUrl, v, `0xce96cb77${padded}`)), // maxWithdraw(address)
	)
	return vaultBalances.reduce((acc, b) => acc + b, raw)
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-522)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })

			// Full liquidity picture: every configured token on every supported chain.
			lpBalances.push(...(await sweepSolverLiquidity(evmRpcUrls, yieldVaults, solver)))
		} catch (err) {
			logger?.warn({ err, filler: bid.filler }, "Failed to process bid for price snapshot")
		}
	}

	if (quotes.length === 0) return null

	// The snapshot reports a single price: the liquidity-weighted median. lowestPrice and
	// highestPrice carry that same value rather than the raw min/max of the bid set, so consumers
	// cannot read an outlier bid as if it were a tradeable bound.
	const medianPrice = weightedMedian(quotes)
```

**File:** sdk/packages/indexer/src/handlers/events/substrateChains/handlePhantomOrderPrices.handler.ts (L124-138)
```typescript
	await PhantomOrderPriceSnapshot.create({
		id: snapshotId,
		commitment,
		tokenA: bytes32ToBytes20(phantom.tokenA),
		tokenB: bytes32ToBytes20(phantom.tokenB),
		// Denormalized from PhantomOrder so a rate (medianPrice / standardAmount) is computable
		// from a single snapshot row without joining back to the order.
		standardAmount: phantom.standardAmount,
		blockNumber,
		lowestPrice: aggregate.lowestPrice,
		highestPrice: aggregate.highestPrice,
		medianPrice: aggregate.medianPrice,
		bidCount: aggregate.bidCount,
		snapshotTime,
	}).save()
```
