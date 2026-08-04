### Title
Liquidity-weighted phantom-order median price can be gamed by transiently inflating solver balance right before the snapshot, mispricing real user swaps - (File: `sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts`)

### Summary
`aggregatePhantomBids` derives the price that the SDK later uses to size real cross-chain intent orders (`getQuote`, `executeBest`) from a **liquidity-weighted median** of solver quotes. Each quote's weight is the solver's *live* `balanceOf` + ERC‑4626 `maxWithdraw` balance, read with a single `eth_call` at the instant the bid window closes. There is no time-weighting, no minimum holding period, and no restriction on depositing/withdrawing around that instant. A solver can therefore transiently inflate its own weight immediately before the snapshot is taken and drain the funds immediately after — the same "deposit → get a better rate → withdraw" pattern described in the source report, applied here to a liquidity-weighted price oracle instead of a utilization-based interest rate.

### Finding Description
`getTotalSolverBalance` computes weight as a point-in-time read: [1](#0-0) 

That weight feeds directly into `weightedMedian`, and the snapshot's `medianPrice` is set equal to whichever quote wins the weighted vote: [2](#0-1) [3](#0-2) 

The snapshot is taken exactly once, at the moment the pallet emits `PhantomBidWindowExhausted` on `on_finalize` — a fully predictable, attacker-known block: [4](#0-3) 

A solver knows the bid-window-close block in advance (it is `created_at_block + phantom_bid_window()`, both on-chain values). It can therefore, in the same or an adjacent transaction bundle on the destination chain:
1. Temporarily balloon its own `balanceOf`/vault `maxWithdraw` for the output token (via a flash loan, a same-block transfer from another address it controls, or a vault deposit) right before the snapshot's `eth_call` is made.
2. Submit (or already have submitted) a bid whose quoted `solverAmount` is deliberately skewed in its own favor.
3. Because its `weight` now dominates the `weightedMedian` calculation, the reported `medianPrice` is dragged toward that solver's self-serving quote instead of reflecting genuine market liquidity.
4. Immediately withdraw/repay the transient balance after the snapshot block.

This is the direct analog of the Sherlock H-8 pattern: a value that should reflect *durable* commitment (real deliverable liquidity, or in the original bug, real deposited principal) is instead read live and can be juiced for a single block/snapshot with no cooldown, no averaging window, and no restriction on the deposit→snapshot→withdraw sequence.

Downstream, this manipulated `medianPrice` is not just informational — it is what the SDK uses to size the amounts of *real* orders that escrow and move user funds: [5](#0-4) 
and it also feeds `getFxPriceFromSnapshots`, which prices other token pairs off the same snapshots: [6](#0-5) 

Unlike `StreamingYieldVault` — which the codebase explicitly hardens against this exact class of attack with a 22h vesting window and a deposit lock (`sdk/packages/core/contracts/vaults/StreamingYieldVault.sol:26-38`) — no equivalent time-lock or averaging exists for the phantom-order weight computation.

### Impact Explanation
`aggregatePhantomBids`'s `medianPrice` directly determines the input/output amounts a user commits to when placing a real IntentGateway order via the SDK (`floor(netInput × medianPrice / standardAmount)` / `ceil(amountOut × standardAmount / medianPrice)`). An attacker who is a permissionless, delegated solver can transiently inflate its measured liquidity around the snapshot instant to skew this price in its own favor, causing genuine users to escrow more input or receive less output than a fair market price would dictate when their order is later filled — a real transaction-manipulation / fund-loss outcome, not merely a display issue, since the mispriced amounts are baked into on-chain escrowed orders.

### Likelihood Explanation
The snapshot block is deterministic and publicly computable in advance (`created_at_block + phantom_bid_window()`), the balance read is a single unauthenticated `eth_call` with no minimum holding duration, and becoming a "solver" only requires EIP-7702-delegating an EOA to the public `SolverAccount` — no governance or permission gate. Any solver capable of a flash loan or a same-block internal transfer can execute this without colluding with a relayer, prover, or admin.

### Recommendation
Do not weight quotes by a single point-in-time balance read. Options: require weight to be measured as a time-averaged or multi-block minimum balance (mirroring `StreamingYieldVault`'s vesting/lock design), require solvers to have held the relevant balance for some minimum duration before the bid window closes, or bound how much a single balance change close to the snapshot block can move the weighted median (e.g., cap weight growth per block, or sample balance at multiple blocks across the bid window and take the minimum).

### Proof of Concept
1. Solver `S` (delegated to the chain's `SolverAccount`) observes a `PhantomOrderRegistered` event and computes the deterministic snapshot block `B = created_at_block + phantom_bid_window()`.
2. In block `B`, `S` submits a bid quoting a self-serving `solverAmount`, and in the same block (or via a flash loan) temporarily transfers/deposits a large balance of the output token into its own address / vault position so that `getTotalSolverBalance(evmRpcUrl, chain, token, S, yieldVaults)` returns an inflated figure at the exact `eth_call` the indexer/SDK performs when `handlePhantomOrderPrices` runs (`sdk/packages/indexer/src/handlers/events/substrateChains/handlePhantomOrderPrices.handler.ts:28-141`).
3. `weightedMedian` (phantom-aggregation.ts:121-136) selects `S`'s quote as the median because its inflated `weight` now carries more than half of `totalWeight`.
4. `S` withdraws/repays the transient balance immediately after the snapshot.
5. A user calling the SDK's quote function (`docs/content/developers/sdk/api/intent-gateway.mdx:228-232`) receives amounts computed from this skewed `medianPrice`, and places a real order that escrows real funds at the manipulated rate.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L115-136)
```typescript
// Liquidity-weighted median of solver quotes. Each quote's influence is proportional to `weight` —
// the solver's total balance for the output token across native + vault venues — so a solver that
// can actually deliver size moves the price more than one quoting on thin liquidity. Returns the
// lower weighted median: the smallest price whose cumulative weight reaches half of the total.
// Zero-weight quotes contribute nothing; if every weight is zero it falls back to the unweighted
// median so a price is still reported.
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

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L354-370)
```typescript
// Sums the solver's redeemable balance of a single token on its destination chain: the raw ERC-20
// balance plus any ERC-4626 vault positions wrapping it.
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

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-508)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L857-875)
```rust
		fn on_finalize(n: BlockNumberFor<T>) {
			// Signal each active commitment on the block its bid window closes so the indexer can
			// aggregate that order's snapshot. Emitted in on_finalize (after all extrinsics) so any
			// bid placed in the window-closing block is already in storage when the snapshot is
			// taken. The bid window is expected to be shorter than the generation interval, so the
			// active batch is never replaced by on_initialize on the same block its window closes.
			let Some(active) = CurrentPhantomOrder::<T>::get() else {
				return;
			};
			let window: BlockNumberFor<T> = Self::phantom_bid_window().into();
			for (commitment, info) in active.iter() {
				if n == info.created_at_block.saturating_add(window) {
					Self::deposit_event(Event::PhantomBidWindowExhausted {
						commitment: *commitment,
						created_at: info.created_at_block,
					});
				}
			}
		}
```

**File:** sdk/packages/indexer/src/services/intentGatewayV3.service.ts (L480-516)
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

		const tokenIsInput = snapshot.tokenA.toLowerCase() === tokenAddress

		const median = new Decimal(snapshot.medianPrice!.toString())
		const standard = new Decimal(snapshot.standardAmount.toString())

		// tokenIsInput: medianPrice is tokenB units received per standardAmount of this token,
		// so its price in stable units is median/standard; otherwise the reciprocal.
		const rate = tokenIsInput
			? median
					.div(new Decimal(10).pow(SNAPSHOT_QUOTE_DECIMALS))
					.div(standard.div(new Decimal(10).pow(decimals)))
			: standard
					.div(new Decimal(10).pow(SNAPSHOT_QUOTE_DECIMALS))
					.div(median.div(new Decimal(10).pow(decimals)))

		return rate
	}

```
