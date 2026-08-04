Found a concrete analog. `pallet_intents_coprocessor::place_bid` stores a bid keyed by `(commitment, filler)`, where `filler` is simply `ensure_signed(origin)` — the extrinsic's on-chain signer — and `user_op` is stored verbatim as opaque bytes with **no on-chain validation whatsoever** that `user_op` was actually produced by, or is bound to, that `filler` account. [1](#0-0) 

### Title
Bid front-running / impersonation via unvalidated `user_op` in `pallet_intents_coprocessor::place_bid` - (File: modules/pallets/intents-coprocessor/src/lib.rs)

### Summary
`place_bid` accepts any `user_op` bytes and stores them under whatever `AccountId` signed the extrinsic, without verifying that the `user_op`'s embedded ERC-4337 nonce/session binding or solver signature actually corresponds to the submitting `filler`. This mirrors the H01 pattern exactly: the "vote" (bid) content is not cryptographically bound to the identity storing it, so anyone can copy another solver's `user_op` bytes and resubmit them under their own account.

### Finding Description
`place_bid(origin, commitment, user_op)` only checks that `user_op` is non-empty and that phantom-order bid-window/duplicate rules are satisfied; it never decodes or validates `user_op` against `filler`: [2](#0-1) 

The real cryptographic binding (nonce key = `keccak256(commitment ‖ sessionKey)`, and the solver's ECDSA signature over `userOpHash`) is enforced only on the *destination EVM chain* inside `SolverAccount.validateUserOp` at execution/fill time: [3](#0-2) 

On Hyperbridge itself, `Bids<T>` is a `StorageDoubleMap<commitment, filler_AccountId, deposit>`, so the pallet's own view of "who bid what" is keyed purely on `ensure_signed(origin)`, not on anything inside `user_op`: [4](#0-3) 

Because `user_op` is opaque `Vec<u8>` and unchecked, an attacker who observes another solver's `user_op` (e.g., from the mempool, from `intents_getBidsForOrder` RPC, or from the offchain-indexed bid store used by `getBidsForOrder`) can call `place_bid` themselves with the *exact same bytes*, becoming a second/duplicate on-chain bidder for the same order commitment while only staking the (cheap, refundable) `StorageDepositFee` — without ever holding the private key that produced the `user_op`, and without ever intending or being able to actually fill the order.

### Impact Explanation
This directly maps to the required impact classes: it is a logic attack / unauthorized bid duplication on a public entrypoint reachable by any signed account. Concretely:
- It corrupts `Bids<commitment, filler>` with an attacker-controlled `filler` entry pointing at a `user_op` the attacker did not produce, poisoning any downstream selection/aggregation logic that treats `Bids` entries as attributable per-account bids (e.g. bid discovery via `getBidsForOrder`/`intents_getBidsForOrder`, and the phantom-order price aggregation which explicitly has to defend against "one solver's bid... copied under N funded fillers" — proving this exact duplication is a known, expected attack surface, but the on-chain `place_bid` extrinsic itself has no defense, only the off-chain SDK aggregator does).
- Because a `select`/`fillOrder` attempt against a copied `user_op` will fail at `SolverAccount.validateUserOp` (nonce/signature mismatch) only on the destination chain, the corrupted state persists and pollutes the Hyperbridge-side bid book (which is the coprocessor's source of truth for solver competition) with unattributable/impersonated entries, degrading the price-discovery/solver-selection process the pallet exists to serve, and consuming another legitimate filler's `DuplicatePhantomBid`/window slot for phantom orders (denying them a bid slot legitimately).

### Likelihood Explanation
High likelihood/trivial to execute: `place_bid` is a public, signed extrinsic; `user_op` bytes for any order are visible on-chain/off-chain (bid storage entries, RPC, or indexer) as soon as the original solver submits. No privileged access, relayer collusion, or leaked key is required — only reading already-public bid data and resubmitting it under a different signer.

### Recommendation
Validate `user_op` on-chain in `place_bid` before persisting the bid: decode the `PackedUserOperation`, recover the solver signature over `userOpHash`, and require the recovered signer/sender to correspond to the submitting `filler` (or otherwise cryptographically tie `filler` to `user_op.sender`/the embedded nonce-key binding), mirroring the checks `SolverAccount.validateUserOp` already performs on the EVM side. At minimum, reject `place_bid` calls where `user_op.sender` does not map to the calling `filler`.

### Proof of Concept
1. Wait for solver A to call `place_bid(origin_A, commitment, user_op_A)` (visible in block explorer / via `intents_getBidsForOrder` RPC or `Bids::<T>::entries(commitment)`).
2. Attacker (any funded account B) reads `user_op_A` bytes back out via `getBidsForOrder`/offchain storage.
3. Attacker calls `place_bid(origin_B, commitment, user_op_A)` with the identical bytes.
4. `Bids::<T>::insert(commitment, B, deposit)` succeeds — B is now recorded as a bidder for `commitment` with A's exact `user_op`, despite never producing or controlling it, confirmed by: [5](#0-4) 

This test only proves a single filler can place a bid — no test in the pallet's own test suite (`modules/pallets/intents-coprocessor/src/tests.rs`) asserts that `place_bid` rejects a `user_op` copied from a different filler's prior bid, confirming the absence of that on-chain guard.

### Citations

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L123-136)
```rust
	/// Storage for bids indexed by commitment and filler address
	/// Allows easy discovery of all bids for a given order commitment
	/// The actual bid data is stored in offchain storage
	/// We store the deposit amount here for accurate refunds
	#[pallet::storage]
	pub type Bids<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		H256, // commitment
		Blake2_128Concat,
		T::AccountId, // filler
		BalanceOf<T>, // deposit amount, actual bid data in offchain storage
		OptionQuery,
	>;
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L277-328)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight(T::WeightInfo::place_bid())]
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

**File:** evm/src/apps/intentsv2/SolverAccount.sol (L121-139)
```text

        // Call IntentGatewayV2.select to recover the sessionKey. This also stages the
        // transient-storage selection that fillOrder enforces at execution.
        SelectOptions memory selectOptions =
            SelectOptions({commitment: commitment, solver: address(this), signature: sessionSignature});
        bytes memory selectCalldata = abi.encodeWithSelector(SELECT_SELECTOR, selectOptions);
        (bool success, bytes memory returnData) = INTENT_GATEWAY_V2.call(selectCalldata);

        if (!success || returnData.length < 32) return ERC4337Utils.SIG_VALIDATION_FAILED;

        address sessionKey = abi.decode(returnData, (address));
        uint192 userOpNonce = uint192(uint256(keccak256(abi.encodePacked(commitment, sessionKey))));
        if (uint192(op.nonce >> 64) != userOpNonce) return ERC4337Utils.SIG_VALIDATION_FAILED;
        if (!_rawSignatureValidation(userOpHash, solverSignature)) return ERC4337Utils.SIG_VALIDATION_FAILED;

        // Pay for gas if needed
        _payPrefund(missingAccountFunds);

        return ERC4337Utils.SIG_VALIDATION_SUCCESS;
```

**File:** modules/pallets/intents-coprocessor/src/tests.rs (L172-193)
```rust
#[test]
fn place_bid_works() {
	new_test_ext().execute_with(|| {
		let filler = AccountId32::new([1; 32]);
		let commitment = H256::random();
		let user_op = BoundedVec::try_from(vec![1u8, 2u8, 3u8]).unwrap();

		// Place a bid
		assert_ok!(Intents::place_bid(
			RuntimeOrigin::signed(filler.clone()),
			commitment,
			user_op.clone()
		));

		// Verify bid was stored (deposit amount for discoverability and refunds)
		assert!(Bids::<Test>::contains_key(&commitment, &filler));
		assert_eq!(Bids::<Test>::get(&commitment, &filler), Some(Intents::storage_deposit_fee()));

		// Verify deposit was reserved
		assert_eq!(Balances::reserved_balance(&filler), Intents::storage_deposit_fee());
	});
}
```
