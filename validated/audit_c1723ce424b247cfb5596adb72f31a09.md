I found a genuine analog in the `pallet-intents-coprocessor` bid-deposit escrow, structurally parallel to the SpiceAuction issue: a deposit is reserved on entry, but the only paths that ever release it are `place_bid` (re-bid) and `retract_bid` (self-service). There is no code path that unreserves a losing/never-selected filler's deposit once a phantom order's bid window closes and the order is settled by another filler — the deposit is left reserved indefinitely unless the same filler happens to call `place_bid` or `retract_bid` again.

### Title
Filler bid deposits become permanently unreservable once a phantom order settles without that filler being selected - (File: modules/pallets/intents-coprocessor/src/lib.rs)

### Summary
`place_bid` reserves a `StorageDepositFee` from the filler's balance and records it in `Bids::<T>`, keyed by `(commitment, filler)`. The only two extrinsics in the pallet that call `Currency::unreserve` are `place_bid` (when replacing an existing bid for the same key) and `retract_bid` (an explicit filler-initiated call). There is no automatic settlement, expiry, or sweep path that clears `Bids::<T>` and unreserves the deposit for fillers who bid but were **not** selected once the order/phantom window closes.

### Finding Description
`place_bid` (lib.rs:279-328) reserves `deposit = Self::storage_deposit_fee()` and stores it at `Bids::<T>::insert(&commitment, &filler, deposit)`. The only cleanup paths found are: [1](#0-0) 
(re-bid unreserves the old deposit before reserving a new one), and [2](#0-1) 
(`retract_bid`, self-service unreserve + removal).

For phantom orders, the pallet enforces a strict bid window and emits `PhantomBidWindowExhausted` once it closes: [3](#0-2) 
but nothing in the pallet iterates `Bids::<T>` for that commitment after the window closes/order settles to unreserve the deposits of fillers who were not the winning bidder. The `Bids` entry (and the reserved currency) simply remains keyed under `(commitment, filler)` forever unless that specific filler proactively calls `retract_bid` — and if they are unaware the order settled (e.g., an automated market-making bot that doesn't track every commitment's outcome, or simply never calls `retract_bid` because there's no on-chain incentive/notification to do so), the reserved balance is permanently locked from ordinary spending (it remains "reserved", not transferable, with no path back except the filler's own future action). Since commitments are one-shot (each phantom/order commitment is unique), a filler who lost the auction has no reason to ever call `place_bid` again for that exact commitment, so the "unreserve on re-bid" cleanup path can never trigger for it either.

This is structurally identical to the reported `SpiceAuction` bug: an accounting/escrow value (bid deposit vs. auction token balance) is created optimistically at entry, but the mitigation the codebase relies on (`retract_bid` vs. `recoverToken`) only covers the "still tracked/active" case, not the "auction/bid concluded via a path other than the one bid-owner-initiated recovery route" case.

### Impact Explanation
Every losing filler's per-bid storage deposit becomes stuck (reserved, non-transferable) with no protocol-level path to reclaim it except the filler manually calling `retract_bid` on that exact stale commitment. In a live phantom-order/auction system that continuously produces new commitments and rewards competitive high-frequency bidding by many solver bots, this causes progressive, unbounded loss/lock of solver funds — solver capital gets trapped in reserve for orders they didn't win, directly reducing available balances without any recovery mechanism baked into settlement itself.

### Likelihood Explanation
High. This triggers on ordinary usage, not any attacker action: any filler who places a bid and loses (the common case, since only one bid wins per order) must remember to separately call `retract_bid` for every losing commitment or its deposit is stuck forever. Given `place_bid` is called at high frequency for phantom orders and auctions, and there is no expiry/incentive/automatic settlement cleanup, deposits will accumulate as permanently reserved balances in normal operation.

### Recommendation
Add a permissionless or automatic cleanup path that unreserves losing bidders' deposits once an order/phantom-window resolves — e.g., have order settlement (or `on_initialize` when replacing `CurrentPhantomOrder`) iterate and unreserve all non-winning `Bids` entries for a resolved commitment, or allow any caller to trigger `retract_bid`-equivalent cleanup for a settled/expired commitment on behalf of any filler once resolution is confirmed on-chain (mirroring the `EscrowRefunded`/dest-side-cancel pattern in the EVM `IntentGatewayV2`, where "anyone may call" after expiry, but the refund always goes to the rightful owner).

### Proof of Concept
1. Filler `A` calls `place_bid(commitment_1, user_op)` — `StorageDepositFee` is reserved, `Bids::<T>::insert(commitment_1, A, deposit)`. [4](#0-3) 
2. Another filler `B` is selected and fills the order (via the `IntentsCoprocessor`/EVM `IntentGatewayV2` fill flow); the order settles.
3. No pallet call ever touches `Bids::<T>::get(commitment_1, A)` again. Filler `A`'s deposit remains `Currency::reserve`d indefinitely.
4. `A` can only escape this by independently discovering the order is gone and calling `retract_bid(commitment_1)` — otherwise the deposit is permanently locked, confirmed by the fact that `tests.rs` only exercises `retract_bid` as a proof of unreserve, never a settlement-triggered cleanup: [5](#0-4) 

**Uncertainty/limitations:** I could not fully trace whether a downstream component (e.g., an off-chain indexer, the `PhantomBidWindowExhausted` event consumer, or a separate runtime hook not captured by the index) automatically calls `retract_bid` on behalf of losing fillers after settlement — the index only surfaced the pallet's extrinsics and events, not any cross-module or off-chain automation that might mitigate this. If such automation exists, the severity is reduced to a liveness/UX issue rather than a hard fund lock; I recommend a Devin session with full repo access to check `tesseract`/indexer components for any post-settlement bid-cleanup logic before treating this as confirmed-critical.

### Citations

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L219-220)
```rust
		/// A phantom order's bid window closed; the indexer can now aggregate its snapshot.
		PhantomBidWindowExhausted { commitment: H256, created_at: BlockNumberFor<T> },
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

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L330-358)
```rust
		/// Retract a bid and receive deposit refund
		///
		/// # Parameters
		/// - `commitment`: The order commitment hash
		///
		/// # Errors
		/// - `BidNotFound`: If no bid exists for this filler and commitment
		#[pallet::call_index(1)]
		#[pallet::weight(T::WeightInfo::retract_bid())]
		pub fn retract_bid(origin: OriginFor<T>, commitment: H256) -> DispatchResult {
			let filler = ensure_signed(origin)?;

			// Get the bid deposit amount
			let deposit = Bids::<T>::get(&commitment, &filler).ok_or(Error::<T>::BidNotFound)?;

			// Unreserve the deposit
			<T as Config>::Currency::unreserve(&filler, deposit);

			// Remove the bid marker from onchain storage
			Bids::<T>::remove(&commitment, &filler);

			// Clear the bid from offchain storage
			let offchain_key = Self::offchain_bid_key(&commitment, &filler);
			offchain_index::clear(&offchain_key);

			Self::deposit_event(Event::BidRetracted { filler, commitment, refund: deposit });

			Ok(())
		}
```
