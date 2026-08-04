## Title
Unauthenticated `address` parameter in `handle_unsigned`/`handle_get_requests` lets anyone mint reputation tokens and claim relayer credit for GET-response delivery - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`pallet-state-coprocessor`'s `handle_get_requests` mints `ReputationAsset` and records response-delivery credit to a raw 32-byte `address` field that is taken verbatim from the caller-supplied `GetRequestsWithProof` payload, with no signature or origin check binding that address to the entity that actually produced or submitted the proof.

### Finding Description
The extrinsic `handle_unsigned` is dispatched with `ensure_none(origin)`, i.e. it is permissionless/unsigned: [1](#0-0) 

Its payload, `GetRequestsWithProof`, carries an attacker-controlled `address: Vec<u8>` field documented as "Address that should be credited with fees": [2](#0-1) 

`handle_get_requests` verifies the source/state proofs for the batch of `GetRequest`s (legitimate, publicly reconstructible cryptographic proofs of already-committed on-chain state), but never verifies that `address` corresponds to the signer/submitter of the transaction or to any authenticated relayer identity: [3](#0-2) 

At the end of the batch, the pallet mints `ReputationAsset` directly to `bytes32` derived from `address`, scaled by the total bytes proven, with the only checks being a non-zero rate and a well-formed 32-byte address — no relation to who actually performed the request/response work or who signed the extrinsic: [4](#0-3) 

The same unauthenticated `address` is also persisted as the attributed relayer on the response receipt and emitted in the `GetRequestHandled` event, which credits the "delivered" event to that same arbitrary address: [5](#0-4) 

This is structurally the same broken invariant as the external report's `mintNFTsForLM`: a callable entrypoint that transfers/creates value to an arbitrary address supplied by the caller, with no check that the caller is the actual, authorized beneficiary. Contrast this with the pallet-relayer outbound-delivery reward path, which explicitly recovers a cryptographic signature from the receipt and checks `signature.signer() == receipt address` before paying `payee` — the exact guard missing here: [6](#0-5) 

### Impact Explanation
Because the state/source proofs required to pass verification are proofs of already-public, already-committed chain state (not secrets only a legitimate relayer could produce), any unprivileged party who observes a pending `GetRequest`/`GetResponse` pair can independently reconstruct the same proof and submit `handle_unsigned` with `address` set to their own account, minting `ReputationAsset` to themselves and claiming the on-chain `GetRequestHandled`/`ReputationMinted` delivery credit — without having relayed anything. This is unauthorized minting/false attribution of a real economic asset (`ReputationAsset`, which per `pallet-messaging-incentives` docs is the same reputation asset shared with mint-per-byte relayer incentives) to a wrong, self-selected beneficiary.

### Likelihood Explanation
The call is unsigned (`ensure_none`) and requires no privileged key, admin role, or relayer registration — any node capable of assembling the ISMP proof data (which is public state, obtainable the same way a legitimate relayer obtains it) can submit this extrinsic directly with an arbitrary `address`. The `validate_unsigned` hook fully re-executes `handle_get_requests` (including proof verification) but does not add any signer-binding check, so it does not close this gap.

### Recommendation
Bind `address` cryptographically to the actual submitter: require a signature (similar to `OutboundRequestDeliveryClaim`/`OutboundConsensusDeliveryClaim`) over the batch commitment, recover the signer, and mint/credit only to the recovered signer's account — never to an unauthenticated caller-supplied byte array.

### Proof of Concept
1. Observe a pending `GetRequest` dispatched by any app; wait until Hyperbridge holds the necessary source/destination state commitments (a normal part of protocol operation, not attacker-controlled).
2. Independently construct the `source` membership proof and `response` state proof for that request (public data, no special relayer credentials required).
3. Submit `state_coprocessor::handle_unsigned(GetRequestsWithProof { requests, source, response, address: <attacker_bytes32> })` as an unsigned transaction.
4. `validate_unsigned` and `handle_get_requests` both pass because all proof checks succeed against real, valid state.
5. `ReputationAsset::mint_into(&attacker_account, amount)` executes, and `GetRequestHandled { relayer: attacker_bytes }` is emitted — crediting the attacker as if they were the relayer, with no signature ever checked against `address`.

### Citations

**File:** modules/pallets/state-coprocessor/src/lib.rs (L90-104)
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
```

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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L105-124)
```rust
		// Ensure the proof height is equal to each retrieval height specified in the Get
		// requests
		if !requests.iter().all(|get| get.height == response.height.height) {
			Err(Error::InsufficientProofHeight)?
		}

		// Verify source proof
		let source_state_machine = validate_state_machine(&host, source.height)?;
		let state_root = host.state_machine_commitment(source.height)?;

		// Verify membership proof to ensure that requests where committed on source chain
		let commitments = requests
			.iter()
			.map(|get| hash_request::<<T as Config>::IsmpHost>(&Request::Get(get.clone())))
			.collect();
		source_state_machine.verify_membership(&host, commitments, state_root, &source)?;

		// Verify response proof
		let dest_state_machine = validate_state_machine(&host, response.height)?;
		let state_root = host.state_machine_commitment(response.height)?;
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-186)
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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L188-234)
```rust
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
```

**File:** docs/outbound-request-incentivization.md (L136-142)
```markdown
9. **State proof verification.** Resolve the destination client with `ismp::handlers::validate_state_machine(&host, height)`, then `verify_withdrawal_proof(state_machine, &state_proof, vec![key])` against hyperbridge's stored state commitment for the destination. A verification failure maps to `OutboundDestinationStateNotKnown` (no commitment at that height), and a missing or null slot value maps to `OutboundDeliveryNotProven`.

10. **Signature attribution.** Recover the signer from `signature.verify(&outbound_request_delivery_message(commitment, destination, payee), None)` and check it matches the address proven in the receipt slot. For EVM, both are 20-byte addresses; for substrate, the bytes from the receipt must equal `signature.signer()`. Mismatch → `OutboundRequestSignerMismatch`.

11. **Payout.** Transfer `reward` from the treasury PalletId account to `payee`.

12. **Persist and emit.** Insert `OutboundRequestsClaimed[commitment] = ()`. Deposit `OutboundRequestDeliveryRewarded { commitment, state_machine: destination, module_id, relayer: payee, amount: reward }`.
```
