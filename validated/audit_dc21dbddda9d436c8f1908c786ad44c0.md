## Analog Found: Unbound `address` field in `pallet-state-coprocessor::handle_get_requests` lets anyone redirect relayer reputation/reward attribution

### Title
Unauthenticated `address` field in `GetRequestsWithProof` allows arbitrary reward/reputation attribution - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
The C04 root cause is a self-reported field (`l1QueueOrigin`) that drives downstream accounting/attribution logic without being cross-checked against the authenticated source of the transaction. The local analog is the `address` field of `GetRequestsWithProof` in `pallet-state-coprocessor`: it determines who is credited as the delivering relayer (`store_response_receipt`) and who is minted reputation tokens for the batch's bandwidth, yet it is never validated against the identity of whoever actually submits the extrinsic or performed the underlying relaying work.

### Finding Description
`handle_get_requests` accepts a `GetRequestsWithProof { requests, source, response, address }` payload and, after verifying the source/response state proofs, uses `address` for two purposes with zero authentication: [1](#0-0) 

1. It is minted reputation tokens proportional to the batch's byte size: [2](#0-1) 

2. It is stored as the relayer of record in the response receipt: [3](#0-2) 

The call site, `handle_unsigned`, is dispatched with `ensure_none(origin)` — there is no signed caller at all — and `validate_unsigned` only re-runs the same proof-verification logic; it never checks that `address` corresponds to the entity submitting the transaction or to any prior on-chain evidence of delivery work: [4](#0-3) 

Contrast this with the sibling reward path in `pallet-relayer`, `OutboundRequestDeliveryClaim`, which was clearly designed with the correct invariant: the claimed `payee` must be the address that a state proof actually shows performed the delivery, verified via a `signature.verify(...)` against the relayer address recovered from the destination's `RequestReceipts` slot: [5](#0-4) 

`handle_get_requests` has no equivalent binding step for `address` — nothing recovers a signer, nothing proves `address` performed any unique service, and nothing prevents the same underlying (publicly reconstructible) `source`/`response` state proofs from being wrapped with an attacker-chosen `address` before the legitimate relayer's submission lands.

### Impact Explanation
This directly produces "wrong beneficiary" fund/reward movement: reputation tokens (from `pallet-messaging-incentives`) that are meant to compensate the party that fetched and proved the cross-chain data can be minted to any address the submitter names, with no requirement that this address correspond to real relaying effort. Because minting is per-batch and keyed only by the caller-supplied `address`, an attacker can repeatedly harvest the reputation-mint budget intended for genuine relayers by resubmitting proofs (which are derivable from public finalized chain state) under attacker-controlled addresses. The `store_response_receipt(&get_response, &address)` call also permanently records an incorrect relayer of record for the batch, corrupting downstream attribution/auditing that depends on that receipt.

### Likelihood Explanation
The pipeline is reachable by any unprivileged party without signing anything (`ensure_none`) — no relayer, prover, admin, or governance role is required. The only barrier is producing valid membership/state proofs, which by design become publicly constructible once the relevant chains finalize the underlying state, so this does not require a malicious peer or leaked key — a plain unprivileged party racing the honest relayer (or simply always submitting first) can consistently redirect the reward to themselves. `validate_unsigned` re-executes the same logic and does not add an identity check, so the txpool provides no additional protection against arbitrary `address` values.

### Recommendation
Bind `address` to a verifiable claim of work, mirroring the model already used in `outbound_request.rs`: require a signature over a message containing the batch commitment(s) and `address`, and recover/validate the signer, or otherwise tie `address` to the account that authored/submitted the transaction (e.g., require a signed extrinsic and use `ensure_signed` origin as the beneficiary instead of an arbitrary payload field). At minimum, reject unsigned submissions and require the submitter's own account to be the credited address.

### Proof of Concept
1. Monitor Hyperbridge for a `GetRequest` whose response can be proven (source and destination chains have advanced past the point where the request/response state is finalized and their state commitments are known to Hyperbridge).
2. Construct the exact same `source` and `response` `Proof`s that the legitimate relayer would use (these are derivable from public chain state/trie data; no privileged access or off-chain secret is required).
3. Set `address` to an attacker-controlled 32-byte account and submit `StateCoprocessor::handle_unsigned(GetRequestsWithProof { requests, source, response, address: attacker })` as an unsigned extrinsic, racing (or simply preceding) the legitimate relayer's submission.
4. On success, `handle_get_requests` mints `total_bytes * MintPerByte` reputation tokens to the attacker's account and records the attacker as the relayer in `response_receipt`, even though the attacker performed none of the off-chain work of servicing the original `GetRequest`. [6](#0-5)

### Citations

**File:** modules/pallets/state-coprocessor/src/impls.rs (L46-55)
```rust
pub struct GetRequestsWithProof {
	/// The associated Get requests
	pub requests: Vec<GetRequest>,
	/// Proof of these requests on the source chain
	pub source: Proof,
	/// State proof of the requested values in the Get requests.
	pub response: Proof,
	/// Address that should be credited with fees
	pub address: Vec<u8>,
}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-192)
```rust
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
```

**File:** modules/pallets/state-coprocessor/src/lib.rs (L90-148)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(1, 2))]
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			message: GetRequestsWithProof,
		) -> DispatchResult {
			ensure_none(origin)?;

			Self::handle_get_requests(message).map_err(|err| {
				log::error!(target: "ismp", "pallet-coprocessor: {:?}", err);
				Error::<T>::HandlingError
			})?;

			Ok(())
		}
	}

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

			let mut messages = message
				.requests
				.iter()
				.map(|get| hash_request::<<T as Config>::IsmpHost>(&Request::Get(get.clone())))
				.collect::<Vec<_>>();
			messages.sort();

			// this is so we can reject duplicate batches at the mempool level
			let msg_hash = sp_io::hashing::keccak_256(&messages.encode()).to_vec();

			Ok(ValidTransaction {
				priority: 100,
				requires: vec![],
				provides: vec![msg_hash],
				longevity: 25,
				propagate: true,
			})
		}
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L169-173)
```rust
		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```
