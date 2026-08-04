Confirmed local analog: `handle_get_requests` in `modules/pallets/state-coprocessor/src/impls.rs` mints reputation to an attacker-chosen `address` field that is never authenticated to the actual submitter, and the extrinsic is unsigned/permissionless.

### Title
Unauthenticated `address` field in `handle_unsigned` lets anyone divert relayer reputation rewards to themselves - (File: `modules/pallets/state-coprocessor/src/lib.rs`, `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`pallet-state-coprocessor::handle_unsigned` is a `ensure_none` (fully unsigned/permissionless) extrinsic that carries a `GetRequestsWithProof { requests, source, response, address }` payload. `Self::handle_get_requests` verifies the `source` and `response` state proofs rigorously (membership/`verify_state_proof`, replay checks, timeouts) but performs **no verification whatsoever** that `address` corresponds to the entity that actually delivered/relayed the `GetResponse`. That `address` is subsequently used to mint reputation tokens via `pallet_messaging_incentives::ReputationAsset`.

### Finding Description
In `handle_get_requests` [1](#0-0) , the function accepts `address: Vec<u8>` as a raw, unauthenticated field of the message and uses it purely as the beneficiary of reputation minting at the end of the function: [2](#0-1) 

Note that `address` is never checked against `msg.signer`/any signature, the relayer that constructed the proof, or any staking/registration record — it's simply attacker-supplied bytes converted to an `AccountId` at mint time: [3](#0-2) 

The call is dispatched via `handle_unsigned`, gated only by `ensure_none(origin)` — i.e., it has no signer at all — and `validate_unsigned` only re-runs `handle_get_requests` for validity and de-duplicates by the hash of the *requests*, not by submitter identity: [4](#0-3) 

This exactly mirrors the reported bug class: a value (`extraProof.root` there, `address` here) that is trusted for a critical effect (fund/asset movement) without being cryptographically bound to the entity that is supposed to be authorized for that effect. In the original report, `extraProof.root` was not tied to `massExitBlock.blockRid`; here, `address` is not tied to who actually performed (or paid gas for) the relaying work, nor to the `msg.signer`/response-receipt relayer recorded elsewhere in the protocol (`host.store_response_receipt(&get_response, &address)` even stores this same unverified address as the "relayer of record").

Because the `GetRequest`/`GetResponse` state (the actual proof data) is all public on-chain information once the underlying requests have been dispatched and answered on the destination chain, any third party can reconstruct the identical `GetRequestsWithProof` message — the proofs themselves are deterministic, chain-derived data, not secrets held by the original relayer — and resubmit it with `address` set to their own account, claiming the reputation mint that should have gone to the party that actually did the querying/relaying work. `require`-style de-duplication exists only for the underlying `requests` hash, so the "provides" tag doesn't protect the address field, and any valid resubmission before the original settles can claim the reward.

### Impact Explanation
This directly falls under the required "reward claims" impact bucket: relayer/reputation rewards are diverted to an unauthorized beneficiary rather than the party that performed the work, i.e., value theft via unauthenticated beneficiary selection. If `pallet_messaging_incentives::ReputationAsset` has any redeemable/transferable economic value (it is minted via `mint_into` on a `fungible`-style asset trait), this is a direct value-theft primitive, not merely a bookkeeping error.

### Likelihood Explanation
The transaction is permissionless (`ensure_none`) and requires no privileged role, no malicious peer/relayer/prover assumption, and no front-running of anyone's private data — the state proofs referenced are public, deterministic data derivable by anyone once the underlying GetRequest/response exists on the source/destination chains. Any unprivileged actor monitoring dispatched `GetRequest`s and public state can independently construct and submit the identical proof payload with their own `address`, so this is a straightforward "unprivileged attacker diverts reward to themselves" path, matching the required impact gate.

### Recommendation
Bind the reward beneficiary to a value that only the legitimate relayer can produce, e.g.:
- Require `address` to be recovered from a signature over the message payload (similar to how `pallet-relayer`'s `beneficiary_details`/`Signature` mechanism authenticates a beneficiary redirect with a nonce-bound signed message), or
- Tie `address` to `msg.signer`/the origin that funded the proof submission, or
- Require the extrinsic to be signed and use the signer's account directly as the reputation beneficiary instead of accepting an arbitrary `address` field.

### Proof of Concept
1. Observe a legitimate `GetRequest` dispatched on Hyperbridge and its corresponding response state materialize on the destination chain (all public data).
2. Independently query the same source/response state proofs (identical to what a legitimate relayer would fetch) and construct a `GetRequestsWithProof { requests, source, response, address: <attacker_account> }`.
3. Submit `state_coprocessor.handle_unsigned(message)` as an unsigned extrinsic before the legitimate relayer's equivalent submission lands (or simply be first, since nothing about identity/timing is enforced beyond the requests-hash dedup key).
4. `handle_get_requests` verifies the proofs (which are valid, since they reference real on-chain state) and proceeds to `mint_into(&relayer, amount)` using the attacker's `address`, and stores `store_response_receipt(&get_response, &address)`, crediting the attacker as the relayer of record and minting reputation to them instead of the actual relayer who performed the underlying work.

### Citations

**File:** modules/pallets/state-coprocessor/src/impls.rs (L62-65)
```rust
	pub fn handle_get_requests(
		GetRequestsWithProof { requests, source, response, address }: GetRequestsWithProof,
	) -> Result<(), Error> {
		// 1. Verify source proofs
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L156-186)
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
