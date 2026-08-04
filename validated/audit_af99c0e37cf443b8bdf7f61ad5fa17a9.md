### Title
Phantom order price oracle can be manipulated via unbacked/cheap liquidity inflation, causing false price acceptance for real IntentGateway swaps - (File: sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts)

### Summary
The reported Flayer bug lets an attacker cheaply manipulate a shared metric (ERC20 `totalSupply`, via free `deposit`/`redeem`) that feeds directly into a rate calculation (`utilizationRate` → `calculateCompoundedFactor`) used to force other users' positions into liquidation for profit. The local analog is Hyperbridge's Phantom order pricing pipeline: a solver's self-reported **on-chain token balance** (`getTotalSolverBalance`) is used, unweighted by any collateralization or cost, as the *weight* in a `weightedMedian` that becomes the `medianPrice` oracle (`PhantomOrderPriceSnapshot`). That `medianPrice` is then used directly to price **real** user swaps via `quoteSnapshot` / `getFxPriceFromSnapshots`, with no sanity bound, no TWAP, and no minimum bid diversity requirement beyond `bidCount > 0`. A solver can temporarily inflate its measured balance (e.g. via a flash loan, temporary transfer-in, or looping through an ERC-4626 vault) at the exact RPC-read instant the aggregation runs, dominate the weighted median with an arbitrarily bad price for its own quoted leg, and have that skewed price become the trusted FX rate that prices subsequent real orders placed by unrelated users.

### Finding Description
`aggregatePhantomBids` (sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts:419-531) computes each verified solver bid's `weight` as `getTotalSolverBalance` — a live `balanceOf` + ERC-4626 `maxWithdraw` read taken via `eth_call` at aggregation time:

```
const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
quotes.push({ price: fillData.solverAmount, weight })
``` [1](#0-0) 

`weightedMedian` then picks the price whose cumulative weight crosses half the total weight — meaning a single dominant-weight solver's quoted price *becomes* the reported price outright:
```
export function weightedMedian(entries: { price: bigint; weight: bigint }[]): bigint {
	...
	if (cumulative * 2n >= totalWeight) return entry.price
``` [2](#0-1) 

This is persisted as the trusted `medianPrice` on `PhantomOrderPriceSnapshot` (sdk/packages/indexer/src/handlers/events/substrateChains/handlePhantomOrderPrices.handler.ts:124-138), which is documented as feeding real order pricing: [3](#0-2) 

Both real quoting paths trust this value with no cross-check against any independent source (Uniswap, oracle, etc.) and no staleness/outlier bound beyond `bidCount > 0` and `medianPrice > 0`:
```
private quoteSnapshot(...): PhantomSnapshotQuoteIntentResult {
    ...
    const amountOut = (netAmountIn * snapshot.medianPrice) / snapshot.standardAmount
``` [4](#0-3) 
```
private static async getFxPriceFromSnapshots(tokenAddress: string, decimals: number): Promise<Decimal | null> {
    ...
    const snapshot = [...asInput, ...asOutput]
        .filter((s) => s.medianPrice && s.medianPrice > 0n && s.standardAmount > 0n)
``` [5](#0-4) 

`validateSnapshot` in the SDK only checks `standardAmount > 0`, `medianPrice > 0`, `bidCount > 0`, and a valid timestamp — none of which prevent an outlier/manipulated price: [6](#0-5) 

The attacker primitive mirrors the Flayer report exactly: a **cheap, self-serve, reversible action** (moving tokens into a balance that's read once, then moving them back out) inflates a metric (`weight`/`totalSupply`) that is the *sole determinant* of a downstream rate (`weightedMedian`/`utilizationRate`) which other, unrelated users' economic outcomes (fill price/liquidation) depend on. No malicious relayer, prover, or governance actor is required — only an unprivileged solver controlling its own EOA balance at the moment the indexer's `eth_call` executes.

### Impact Explanation
If a solver can win the weighted median with an inflated balance, it can set the `PhantomOrderPriceSnapshot.medianPrice` to any price of its choosing on its own leg, since it need not actually be outbid by honest liquidity providers. Because `medianPrice` is used verbatim to price **real, unrelated** user orders (`quoteSnapshot`, `getFxPriceFromSnapshots`), the attacker can:
1. Set the price artificially low, so real users placing exact-input orders receive far less output than fair value, or exact-output orders are made to pay far more input than fair value — the attacker (or a colluding solver) then fills at the below-market rate and pockets the difference.
2. Corrupt `IntentGatewayTokenVolume`/`CumulativeIntentGatewayVolumeUSD` USD-rollups that rely on the same FX price, distorting protocol-level economics reporting.

This is a direct false-price-acceptance / fund-loss vector matching the bounty's "logic attacks" and "false proof/state acceptance" categories, since the price is treated as ground truth for settlement math without any anchor to real market data.

### Likelihood Explanation
Medium-to-high. The attacker needs: (a) to be a solver delegated to the chain's `SolverAccount` (a routine setup step, not privileged), (b) to control a balance-inflating action (self-transfer, flash loan, or a same-block deposit/withdraw against an ERC-4626 vault or any funding source) timed against the indexer's aggregation window, which is triggered deterministically by `PhantomBidWindowExhausted`. Because the balance check is a plain `eth_call` read with no time-weighting (no TWAP, no minimum holding period), a single-block manipulation suffices. The main friction is needing to be a delegated `SolverAccount` and knowing the aggregation timing, both of which are discoverable from public contract state and on-chain events.

### Recommendation
- Do not let a single solver's weight dominate the weighted median outright; cap per-solver weight contribution (e.g., no solver may contribute more than X% of total weight) so one bidder cannot single-handedly set the price.
- Require the balance used as weight to reflect durable liquidity rather than an instantaneous read — e.g., average balance over a window, or require balance to have been held for N blocks prior to the snapshot.
- Cross-check `medianPrice` against an independent reference (the existing Uniswap V4 quote path, or a bounded deviation check similar to the `priceGuard`/`maxDeviationBps` mechanism already used in `sdk/packages/simplex/src/strategies/fx.ts`) before persisting/using a Phantom snapshot for real order quoting, rejecting snapshots that deviate too far from the reference.
- Increase the minimum diverse `bidCount`/unique-weight-source requirement so a single large balance cannot satisfy the "at least one bid" threshold alone.

### Proof of Concept
1. Attacker deploys/controls a `SolverAccount`-delegated EOA (`Solver`) on the destination chain for a Phantom-priced pair (e.g., USDC→cNGN).
2. Attacker submits an honest-looking bid quoting a favorable `solverAmount` for the leg it wants to win (e.g., a very low cNGN-per-USDC price).
3. Immediately before the aggregation's `eth_call` balance read (triggered by `PhantomBidWindowExhausted`), the attacker inflates its `cNGN`/vault balance via a flash loan or temporary transfer so `getTotalSolverBalance` returns a value far exceeding all honest solvers' combined weight.
4. `aggregatePhantomBids` computes `weightedMedian` and returns the attacker's quoted price as `medianPrice` since its weight alone crosses 50% of total weight (sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts:121-135, 505-522).
5. The attacker immediately unwinds the flash loan / withdraws the temporary balance.
6. `PhantomOrderPriceSnapshot.medianPrice` is now the attacker's chosen price and is served to `quoteIntent()` for all subsequent real users, who receive a mispriced quote (sdk/packages/sdk/src/protocols/intents/quote/phantomSnapshot.ts:83-101; sdk/packages/indexer/src/services/intentGatewayV3.service.ts:480-515) that a colluding/second-account solver can then fill at the manipulated rate for guaranteed profit.

Note: I could not directly inspect the Rust-side `pallet-intents-coprocessor` bid-window/`intents_getBidsForOrder` RPC implementation in this pass (only its type references were indexed), so the exact timing granularity of `PhantomBidWindowExhausted` and whether any additional server-side sanity checks exist there is unverified — a Devin session with full repo access should confirm this before treating the PoC as final.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L121-135)
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
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-509)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })

```

**File:** sdk/packages/indexer/src/configs/schema.graphql (L2333-2337)
```text
	Liquidity-weighted median output amount across all valid bids, in the smallest unit of the
	output token (tokenB). Each bid is weighted by the solver's total balance for that token
	(native + ERC-4626 vault venues), so solvers that can actually deliver size influence the
	median more than those quoting on thin liquidity. Null if no valid bids were found.
	"""
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/phantomSnapshot.ts (L83-94)
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
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/phantomSnapshot.ts (L132-145)
```typescript
	private validateSnapshot(snapshot: PhantomOrderPriceSnapshot): void {
		if (snapshot.standardAmount <= 0n) {
			throw new InvalidPhantomSnapshotError(snapshot.commitment, "standardAmount must be greater than zero")
		}
		if (snapshot.medianPrice <= 0n) {
			throw new InvalidPhantomSnapshotError(snapshot.commitment, "medianPrice must be greater than zero")
		}
		if (snapshot.bidCount <= 0) {
			throw new InvalidPhantomSnapshotError(snapshot.commitment, "bidCount must be greater than zero")
		}
		if (Number.isNaN(snapshot.snapshotTime.getTime())) {
			throw new InvalidPhantomSnapshotError(snapshot.commitment, "snapshotTime is invalid")
		}
	}
```

**File:** sdk/packages/indexer/src/services/intentGatewayV3.service.ts (L480-497)
```typescript
	private static async getFxPriceFromSnapshots(tokenAddress: string, decimals: number): Promise<Decimal | null> {
		const [asInput, asOutput] = await Promise.all([
			PhantomOrderPriceSnapshot.getByFields([["tokenA", "=", tokenAddress]], {
				limit: SNAPSHOT_SCAN_LIMIT,
				orderBy: "blockNumber",
				orderDirection: "DESC",
			}),
			PhantomOrderPriceSnapshot.getByFields([["tokenB", "=", tokenAddress]], {
				limit: SNAPSHOT_SCAN_LIMIT,
				orderBy: "blockNumber",
				orderDirection: "DESC",
			}),
		])

		const snapshot = [...asInput, ...asOutput]
			.filter((s) => s.medianPrice && s.medianPrice > 0n && s.standardAmount > 0n)
			.sort((a, b) => (a.blockNumber > b.blockNumber ? -1 : 1))[0]
		if (!snapshot) return null
```
