### Title
Phantom Order Price Snapshot Uses Spot (Point-in-Time) Solver Balances as Price Weight, Letting a Solver Freely Manipulate the Quote Used for Real Intent Settlement - (File: `sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts`)

### Summary
The core broken invariant in H-4 is: a security-critical parameter is derived from an instantaneous, freely-manipulable on-chain quantity (liquidity in one tick, read at `update()` time), which an attacker can inflate for the cost of gas only, via a deposit-then-withdraw cycle timed around the read. The same pattern exists in Hyperbridge's phantom-order price-snapshot pipeline: the weight assigned to each solver's bid in the liquidity-weighted median price is that solver's **current spot ERC-20/vault balance**, read via RPC exactly when the bid window closes. Because the balance check has no time-averaging and is triggered by a public on-chain event the attacker can watch, a solver can transiently inflate its own balance right before the snapshot is computed (e.g., via a flash loan or a large but temporary transfer) and withdraw it immediately after, at effectively zero cost. This directly corrupts `medianPrice`, which downstream is used verbatim to price **real** user intents in `PhantomSnapshotIntentQuoteStrategy`.

### Finding Description
`aggregatePhantomBids` computes, for every verified bid, a `weight` equal to `getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)` — a live read of the solver's raw ERC-20 balance plus ERC-4626 vault positions on the destination chain at the moment the aggregation runs: [1](#0-0) 

These `{price, weight}` pairs feed `weightedMedian(quotes)` to produce a single `medianPrice`, which is persisted as the trusted snapshot: [2](#0-1) 

The aggregation is triggered by the on-chain `PhantomBidWindowExhausted` event — a public, predictable signal — after which the indexer immediately queries live bids and each solver's balance via RPC: [3](#0-2) 

The schema comment itself documents that the median is "liquidity-weighted... so solvers that can actually deliver size influence the median more than those quoting on thin liquidity," and that no fill simulation is done because weighting substitutes for it: [4](#0-3) 

This `medianPrice` is not a synthetic-only value — it directly prices **real** cross-chain intents. `PhantomSnapshotIntentQuoteStrategy.quoteSnapshot` computes actual `amountOut`/`amountIn` for real orders using `snapshot.medianPrice / snapshot.standardAmount`: [5](#0-4) 

The chain of causation mirrors H-4 exactly:
1. A value the protocol treats as a trusted risk/pricing input (IV in Aloe; `medianPrice` here) is derived from a **spot, single-block on-chain read** (tick liquidity in Aloe; ERC-20/vault balance here).
2. The read is manipulable by depositing capital immediately before the read and withdrawing immediately after, at zero cost beyond gas (and a flash-loan fee, which is negligible).
3. `IV_CHANGE_PER_UPDATE`-style incremental limits don't exist here at all — there's no rate limiting or time-weighting on the balance read, so a single manipulation fully swings the weight in one shot.
4. The corrupted output feeds a downstream financial decision (LTV in Aloe; a real trade's `amountIn`/`amountOut` here) that moves real funds.

### Impact Explanation
A solver participating in the phantom-order bidding process can inflate its own weight in `weightedMedian` by transiently boosting its balance of the output token right before the bid window closes and the indexer reads balances, then draining it back out afterward. Because the weighting has no time-window smoothing (unlike a TWAP), this lets the attacker:
- Push its own quote to dominate the weighted median, pulling `medianPrice` toward whatever price the attacker bid, even if that price is far from any real market rate.
- Since `medianPrice` is directly used to compute swap amounts for real users' USDC↔cNGN intents via `PhantomSnapshotIntentQuoteStrategy`, this can cause users to receive quotes at a manipulated exchange rate — either overpaying (value extracted by the manipulating solver/filler who later fills at the skewed rate) or the protocol accepting a bogus price as ground truth for settlement sizing. This is a false-price acceptance that leads directly to wrong amounts moving between real counterparties in intent settlement, matching the bounty's "false proof/state acceptance" and "transaction manipulation / wrong amount" categories.

### Likelihood Explanation
The only precondition is that the attacker operates (or acquires) a solver identity that is EIP-7702-delegated to the configured `SolverAccount` — a status any participant in the permissionless filler/solver role can obtain, not a privileged/relayer/admin role. The manipulation requires no compromised keys, no malicious relayer, and no governance action — only capital that can be moved in and out around a publicly observable, deterministic event (`PhantomBidWindowExhausted`). Flash loans on EVM chains make the capital requirement negligible. This is a realistic, repeatable, self-serve attack path exercisable by any funded solver.

### Recommendation
Do not weight bids by an instantaneous spot balance read at aggregation time. Use a time-weighted or historical average of the solver's balance over a window preceding the bid-window close (mirroring the H-4 fix of using TWAL — time-weighted average liquidity — instead of spot liquidity), or require the balance to be locked/attested (e.g., staked collateral with an unbonding period) rather than freely transferable at will. At minimum, sample balance multiple times across the bid window and take a minimum or average rather than a single point-in-time read, so a flash-in/flash-out cannot dominate the weighting for a single sample.

### Proof of Concept
1. Attacker deploys/controls a solver account delegated to the destination chain's `SolverAccount` (satisfying `isVerifiedSolverBid`).
2. Attacker watches for the phantom order's bid window opening (`PhantomOrderRegistered`) and its scheduled close after `PhantomBidWindow` blocks — a fixed, publicly known interval (`IntentPhantomOrderBidWindow`/`IntentsPhantomOrderBidWindow` config): [6](#0-5) 
3. Attacker submits a bid (`place_bid`) quoting a favorable price for itself: [7](#0-6) 
4. Immediately before the window closes (predictable block), attacker flash-loans a large amount of the output token (or an ERC-4626 vault position) into its solver wallet on the destination chain.
5. `PhantomBidWindowExhausted` fires; the indexer's handler runs `aggregatePhantomBids`, which reads the attacker's inflated balance via `getTotalSolverBalance` and assigns it as `weight` for the attacker's quote: [1](#0-0) 
6. Attacker repays the flash loan in the same transaction/block, restoring its balance to normal — net cost is gas plus flash-loan fee only.
7. The resulting `PhantomOrderPriceSnapshot.medianPrice` is now skewed toward the attacker's bid price and is subsequently used verbatim by `PhantomSnapshotIntentQuoteStrategy.quoteSnapshot` to compute real users' swap amounts: [8](#0-7) 

Note: I was unable to fully inspect the internals of `weightedMedian` and `getTotalSolverBalance` (their exact averaging/weighting arithmetic) within the available context window, so the precise magnitude of price skew achievable per unit of flash-borrowed capital is not independently confirmed — but the structural flaw (spot balance as trust input, no time-weighting, publicly predictable read timing) is directly evidenced by the cited code and is sufficient to establish the manipulation primitive.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-511)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })

			// Full liquidity picture: every configured token on every supported chain.
			lpBalances.push(...(await sweepSolverLiquidity(evmRpcUrls, yieldVaults, solver)))
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L517-530)
```typescript
	if (quotes.length === 0) return null

	// The snapshot reports a single price: the liquidity-weighted median. lowestPrice and
	// highestPrice carry that same value rather than the raw min/max of the bid set, so consumers
	// cannot read an outlier bid as if it were a tradeable bound.
	const medianPrice = weightedMedian(quotes)

	return {
		lowestPrice: medianPrice,
		highestPrice: medianPrice,
		medianPrice,
		bidCount: quotes.length,
		lpBalances,
	}
```

**File:** sdk/packages/indexer/src/handlers/events/substrateChains/handlePhantomOrderPrices.handler.ts (L25-39)
```typescript
// Triggered by PhantomBidWindowExhausted once a phantom order's bid window closes, so every bid is
// already in. Aggregates that single order's bids into one price snapshot. The heavy lifting lives in
// aggregatePhantomBids(); this handler just resolves endpoints and persists the result.
export const handlePhantomOrderPrices = wrap(async (event: SubstrateEvent): Promise<void> => {
	const blockNumber = event.block.block.header.number.toBigInt()
	const blockHash = event.block.block.header.hash.toString()

	const [commitmentData] = event.event.data
	const commitment = commitmentData.toHex()

	const phantom = await PhantomOrder.get(commitment)
	if (!phantom) return

	const snapshotId = `${commitment}-${blockNumber}`
	if (await PhantomOrderPriceSnapshot.get(snapshotId)) return
```

**File:** sdk/packages/indexer/src/configs/schema.graphql (L2290-2338)
```text
"""
The price snapshot for a phantom order, written once its bid window closes (signalled by the
PhantomBidWindowExhausted event). It collects all live bids via intents_getBidsForOrder and keeps
only those from our own solvers: a bid counts when its user operation carries a solver signature
over this order's userOpHash that recovers to the sender, and that sender is EIP-7702-delegated to
the chain's SolverAccount. It then records the output amount distribution across the surviving bids,
weighting each by the solver's liquidity. A snapshot is only written when at least one bid passes
verification and an EVM RPC endpoint is configured for the phantom order's destination chain.
"""
type PhantomOrderPriceSnapshot @entity {
	"""
	Composite identifier: {commitment}-{blockNumber}.
	"""
	id: ID!

	"""
	The phantom order commitment this snapshot belongs to. References PhantomOrder.id.
	"""
	commitment: String! @index

	"""
	EVM address of the input token (tokenA) priced by this snapshot, as a 0x-prefixed hex string.
	"""
	tokenA: String! @index

	"""
	EVM address of the output token (tokenB) the prices are denominated in, as a 0x-prefixed hex string.
	"""
	tokenB: String! @index

	"""
	Standard input amount (smallest unit of tokenA) the prices are quoted against. Denormalized from
	PhantomOrder so the exchange rate (medianPrice / standardAmount, adjusted for token decimals) is
	computable from a single snapshot row without joining back to the order.
	"""
	standardAmount: BigInt!

	"""
	Hyperbridge block number at which this snapshot was taken.
	"""
	blockNumber: BigInt! @index

	"""
	Liquidity-weighted median output amount across all valid bids, in the smallest unit of the
	output token (tokenB). Each bid is weighted by the solver's total balance for that token
	(native + ERC-4626 vault venues), so solvers that can actually deliver size influence the
	median more than those quoting on thin liquidity. Null if no valid bids were found.
	"""
	medianPrice: BigInt @index
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/phantomSnapshot.ts (L83-101)
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
	}
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L106-109)
```rust
parameter_types! {
	pub const IntentStorageDepositFee: Balance = 100 * EXISTENTIAL_DEPOSIT;
	pub const IntentPhantomOrderBidWindow: u32 = 5;
}
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L279-328)
```rust
		pub fn place_bid(
			origin: OriginFor<T>,
			commitment: H256,
			user_op: BoundedVec<u8, ConstU32<1_048_576>>,
		) -> DispatchResult {
			let filler = ensure_signed(origin)?;

			// Validate user_op is not empty
			ensure!(!user_op.is_empty(), Error::<T>::InvalidUserOp);

			// Phantom orders have stricter rules: one bid per filler, no updates, and only
			// within the configured acceptance window after the order was registered. Every
			// active pair is checked, not just the most recently generated one.
			if let Some(active) = CurrentPhantomOrder::<T>::get() {
				if let Some((_, info)) = active.iter().find(|(c, _)| *c == commitment) {
					let window: BlockNumberFor<T> = Self::phantom_bid_window().into();
					ensure!(
						frame_system::Pallet::<T>::block_number() <= info.created_at_block + window,
						Error::<T>::PhantomOrderBidWindowClosed
					);
					ensure!(
						!Bids::<T>::contains_key(&commitment, &filler),
						Error::<T>::DuplicatePhantomBid
					);
				}
			}

			// If a bid already exists, unreserve the old deposit first
			if let Some(old_deposit) = Bids::<T>::get(&commitment, &filler) {
				<T as Config>::Currency::unreserve(&filler, old_deposit);
			}

			let deposit = Self::storage_deposit_fee();

			// Reserve the new deposit
			<T as Config>::Currency::reserve(&filler, deposit)
				.map_err(|_| Error::<T>::InsufficientBalance)?;

			// Store the bid in offchain storage
			let bid = Bid { filler: filler.clone(), user_op: user_op.to_vec() };
			let offchain_key = Self::offchain_bid_key(&commitment, &filler);
			offchain_index::set(&offchain_key, &bid.encode());

			// Store deposit amount in onchain storage for discoverability and accurate refunds
			Bids::<T>::insert(&commitment, &filler, deposit);

			Self::deposit_event(Event::BidPlaced { filler, commitment, deposit });

			Ok(())
		}
```
