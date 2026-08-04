## Analysis Summary

Reducing the mempool report to its core invariant: **transaction-pool-level validation logic must be side-effect-free / idempotent**, because pool-level checks run outside the atomic dispatch boundary and can be invoked more than once for the same request. In Monad this manifested as unmetered mempool growth; the Hyperbridge analog is a pallet that performs its *entire* state-mutating handler — bandwidth debits, reputation minting, response-receipt storage, and MMR insertion — directly inside `ValidateUnsigned::validate_unsigned`, instead of confining mutation to the dispatchable call body.

### Title
Full state mutation executed inside `validate_unsigned` allows duplicate bandwidth debit, reputation mint, and response commitment for one submitted `GetRequestsWithProof` batch — (File: `modules/pallets/state-coprocessor/src/lib.rs`)

### Summary
`pallet-state-coprocessor`'s unsigned extrinsic `handle_unsigned` is guarded by a `ValidateUnsigned` impl whose `validate_unsigned` calls `Self::handle_get_requests(message.clone())` — the exact same function invoked later by the dispatchable body — rather than performing only stateless well-formedness checks. [1](#0-0) 
This function is not read-only: it verifies proofs, drains bandwidth subscriptions via `BandwidthGate::try_consume`, mints reputation tokens, stores the response receipt, and pushes a leaf into the MMR. [2](#0-1) 

### Finding Description
`Executive::apply_extrinsic` calls the transaction's `validate_unsigned`/validation path in the *real, persisting* storage context immediately before dispatch — this is why FRAME's own convention for `ValidateUnsigned` is to keep `validate_unsigned` and `pre_dispatch` free of storage mutation and defer all effects to the call body. This pallet violates that convention: the comment "`empty pre_dispatch so we don't modify storage`" at line 116 shows the authors were aware of the risk but placed the mutation in `validate_unsigned` instead. [3](#0-2) 

Consequences of calling `handle_get_requests` in `validate_unsigned`:
1. Bandwidth is drained via `BandwidthGate::try_consume` for each `GetRequest`, and reputation is minted to the attacker-controlled `address` field of the message. [4](#0-3) 
2. `host.store_response_receipt` and `Self::dispatch_get_response` (which pushes an MMR leaf, sets `Responded`, and emits `Response`/`GetRequestHandled` events) execute as part of validation, before the extrinsic is actually dispatched. [5](#0-4) 
3. When `apply_extrinsic` proceeds to actually dispatch `handle_unsigned`, it calls `handle_get_requests` a second time. The re-entry hits the pallet's own duplicate-response guard (`host.response_receipt(&probe).is_some()` → `Error::DuplicateResponse`), which triggers on the second call because the receipt was already written during validation. [6](#0-5) 

Because only the dispatchable body is wrapped in FRAME's automatic "revert storage on `Err`" transactional boundary, the failing second call rolls back nothing from the *first* (validation-time) execution. The net effect for a single submitted `handle_unsigned` extrinsic is: bandwidth debited once, reputation minted once, response receipt/MMR leaf/events committed once — all *persisted* — while the extrinsic itself surfaces as failed/erroring (`Error::HandlingError`) on-chain. An unprivileged submitter (anyone who can gossip an unsigned extrinsic with a validly-provable `GetRequestsWithProof`) can exploit the asymmetry: because `validate_unsigned` is also re-invoked by the transaction pool every time it revalidates queued transactions against new blocks, and because block authors may re-attempt inclusion, the same message can be replayed through `validate_unsigned` multiple times across pool revalidation cycles before/if it is ever included, each time (if the account's bandwidth allowance and consensus proofs remain valid) minting reputation and burning bandwidth again — a duplicate-settlement primitive matching the bounty's "replay/double-claim/double-settlement" category, driven from an unauthenticated (`ensure_none`) code path.

### Impact Explanation
This breaks the "moved exactly once" invariant for both bandwidth balances and relayer reputation rewards: an attacker-controlled `address` field receives reputation mints and bandwidth-subscription debits from validation-time execution that is decoupled from the final, user-visible dispatch outcome, enabling double-crediting of relayer rewards and inconsistent bandwidth accounting without needing a malicious relayer, prover, or governance actor — only a well-formed proof payload.

### Likelihood Explanation
Any account able to submit unsigned extrinsics (open to the public, gated only by `ensure_none`) can trigger this by submitting a `GetRequestsWithProof` with valid consensus/state proofs; no privileged role, colluding relayer, or race condition beyond ordinary transaction-pool revalidation/inclusion mechanics is required. The exact number of extra mints depends on pool revalidation frequency, which is influenceable but not attacker-controlled with certainty — this is the main source of uncertainty in precisely bounding the multiplier, though the core double-mutation-per-inclusion-attempt is directly demonstrated by the code path itself.

### Recommendation
Remove all state mutation from `validate_unsigned`. It should perform only stateless structural/format checks (decode validity, basic bounds) and construct the `ValidTransaction` tag; move `Self::handle_get_requests` invocation exclusively into the `handle_unsigned` call body so mutation only ever happens once, inside the properly transactional dispatch path.

### Proof of Concept
1. Craft a `GetRequestsWithProof` with a valid source/response proof for a `GetRequest` batch and set `address` to the attacker's own 32-byte account.
2. Submit it as an unsigned extrinsic calling `handle_unsigned`.
3. During `apply_extrinsic`, `validate_unsigned` runs `handle_get_requests` against live storage: bandwidth is debited, reputation minted to `address`, response receipt stored, MMR leaf pushed, events emitted — all persisted. [1](#0-0) 
4. Dispatch then calls `handle_unsigned` → `handle_get_requests` again; the duplicate-response check now fires (`Error::DuplicateResponse`), so the extrinsic ultimately errors, but the storage effects from step 3 remain committed. [6](#0-5) 
5. If the same extrinsic is revalidated again by the pool (still holding valid proofs/allowance) before being dropped, `validate_unsigned` executes the full mutation again, minting additional reputation and consuming additional bandwidth for the same logical response.

### Citations

**File:** modules/pallets/state-coprocessor/src/lib.rs (L107-129)
```rust
	#[pallet::validate_unsigned]
	impl<T: Config> ValidateUnsigned for Pallet<T>
	where
		T::AccountId: AsRef<[u8]>,
		<T as frame_system::Config>::AccountId: From<[u8; 32]>,
		<T as pallet_ismp::Config>::Balance: Into<u128>,
	{
		type Call = Call<T>;

		// empty pre-dispatch so we don't modify storage
		fn pre_dispatch(_call: &Self::Call) -> Result<(), TransactionValidityError> {
			Ok(())
		}

		fn validate_unsigned(_source: TransactionSource, call: &Self::Call) -> TransactionValidity {
			let Call::handle_unsigned { message } = call else {
				return Err(TransactionValidityError::Invalid(InvalidTransaction::Call));
			};

			if let Err(err) = Self::handle_get_requests(message.clone()) {
				log::error!(target: "ismp", "{:?}", err);
				return Err(TransactionValidityError::Invalid(InvalidTransaction::Call));
			}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L94-102)
```rust
			// This request has already been responded to. Mirror `handlers/response.rs:61`:
			// dedup against `response_receipt`, which the dispatch path writes for this exact
			// GetRequest hash after producing a response. The receipt also binds the response
			// commitment, so external auditors can attest "Hyperbridge produced response X for
			// request Y" from one map.
			let probe = GetResponse { get: req.clone(), values: Default::default() };
			if host.response_receipt(&probe).is_some() {
				Err(Error::DuplicateResponse { meta: (&probe).into() })?
			}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L140-237)
```rust
			let response = GetResponse { get: req, values };

			// Meter the app's bandwidth using the full size of the
			// abi-encoded GetResponse. Charged after proof verification
			// so the value sizes are final.
			let bytes = ismp::abi::encode_get_response(&response).len() as u32;
			<T as Config>::BandwidthGate::try_consume(
				&response.get.source,
				&response.get.from,
				bytes,
			)
			.map_err(|err| Error::Custom(alloc::format!("bandwidth gate: {err}")))?;
			total_bytes = total_bytes.saturating_add(bytes);

			responses.push(response);
		}

		// Mint reputation tokens to the named relayer. The address is the
		// relayer's raw 32-byte public key as supplied by the coprocessor.
		// A zero rate disables minting and a malformed address simply skips
		// the mint — we don't want a non-32-byte address to fail the whole
		// batch since the response insertion below has no dependency on it.
		// The per-byte rate and reputation asset are inherited from
		// `pallet-messaging-incentives` so both pallets share one source of truth.
		let rate = pallet_messaging_incentives::MintPerByte::<T>::get();
		if !rate.is_zero() && total_bytes > 0 {
			if let Ok(bytes32) = <[u8; 32]>::try_from(address.as_slice()) {
				let relayer: T::AccountId = bytes32.into();
				let bytes_balance: BalanceOf<T> = (total_bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if !amount.is_zero() {
					match <T as pallet_messaging_incentives::Config>::ReputationAsset::mint_into(
						&relayer, amount,
					) {
						Ok(_) => Pallet::<T>::deposit_event(Event::ReputationMinted {
							relayer,
							bytes: total_bytes,
							amount,
						}),
						Err(err) => log::warn!(
							target: "ismp",
							"state-coprocessor: reputation mint failed for {total_bytes}b: {err:?}",
						),
					}
				}
			}
		}

		for get_response in responses {
			host.store_response_receipt(&get_response, &address)?;
			Self::dispatch_get_response(get_response, address.clone())
				.map_err(|_| Error::Custom("Failed to dispatch get response".to_string()))?;
		}

		Ok(())
	}

	/// Insert a get response into the MMR and emits an event
	pub fn dispatch_get_response(
		get_response: GetResponse,
		address: Vec<u8>,
	) -> Result<(), ismp::Error> {
		let commitment = hash_get_response::<<T as Config>::IsmpHost>(&get_response);
		let req_commitment =
			hash_request::<<T as Config>::IsmpHost>(&Request::Get(get_response.get.clone()));
		let event = pallet_ismp::Event::Response {
			request_nonce: get_response.get.nonce,
			dest_chain: get_response.get.source,
			source_chain: get_response.get.dest,
			commitment,
			req_commitment,
		};

		let leaf_index_and_pos = <T as Config>::Mmr::push(Leaf::GetResponse(get_response));
		let meta = FeeMetadata::<T> { payer: [0u8; 32].into(), fee: Default::default() };

		pallet_ismp::child_trie::ResponseCommitments::<T>::insert(
			commitment,
			RequestMetadata {
				offchain: LeafIndexAndPos {
					leaf_index: leaf_index_and_pos.index,
					pos: leaf_index_and_pos.position,
				},
				fee: meta,
				claimed: true,
			},
		);
		pallet_ismp::Responded::<T>::insert(req_commitment, true);
		pallet_ismp::Pallet::<T>::deposit_event(event.into());
		let event = pallet_ismp::Event::GetRequestHandled(RequestResponseHandled {
			commitment: req_commitment,
			relayer: address.clone(),
		});

		pallet_ismp::Pallet::<T>::deposit_event(event.into());

		Ok(())
	}
```
