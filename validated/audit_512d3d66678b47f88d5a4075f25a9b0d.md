This is the finding: the ordinary `pallet-messaging-incentives::FeeHandler::on_executed` path derives the minted `relayer` cryptographically from the sr25519 signature embedded in the delivered `Message` [1](#0-0)  — the beneficiary is unforgeable because it's recovered from a signature, not supplied as free-form input. `pallet-state-coprocessor::handle_get_requests`, however, mints the same `ReputationAsset` using an `address: Vec<u8>` field that is taken verbatim from the caller-supplied `GetRequestsWithProof` payload, with no signature check binding it to whoever produced/verified the proof [2](#0-1) [3](#0-2) . The call is `ensure_none`-gated (fully unsigned) [4](#0-3) , so any unprivileged party can name themselves as `address` and mint reputation for a batch, without ever having relayed anything on the origin/destination chain.

### Title
Unbound reputation minting via unauthenticated `address` field in `pallet-state-coprocessor::handle_get_requests` - (File: modules/pallets/state-coprocessor/src/impls.rs)

### Summary
`handle_unsigned`/`handle_get_requests` mints `ReputationAsset` (the same fungible incentive asset paid out elsewhere for genuine relaying work) to an arbitrary `address: Vec<u8>` supplied inside the unsigned `GetRequestsWithProof` payload, with no cryptographic binding between that address and the entity that actually fetched/verified the state proof.

### Finding Description
`GetRequestsWithProof::address` is described as "the relayer's raw 32-byte public key as supplied by the coprocessor" [5](#0-4) , but the pallet never verifies this. The extrinsic is unsigned (`ensure_none(origin)`) [6](#0-5) , and `validate_unsigned` only checks the message-hash for mempool dedup, not the `address` field [7](#0-6) . Inside `handle_get_requests`, once membership/state proofs verify, the pallet computes `total_bytes` and unconditionally mints `rate.saturating_mul(bytes_balance)` of `ReputationAsset` to whatever 32-byte value is in `address` [8](#0-7) .

Contrast this with the sibling reward path in `pallet-messaging-incentives`, which recovers the minted beneficiary from an sr25519 signature embedded in the ISMP `Message` itself (`relayer_for`) — an attacker cannot rewrite that beneficiary without forging a signature [9](#0-8) . `pallet-state-coprocessor` has no equivalent check, so the guard that exists everywhere else in the reward-claim surface (see also the outbound-consensus/outbound-request claim pipelines, which recover and check a signature before paying a reward, e.g. [10](#0-9) ) is simply absent here.

### Impact Explanation
Anyone can dispatch a self-serve `GetRequest` (e.g. querying an arbitrary cheap storage key on any connected chain your own account controls or that is publicly queryable), obtain the resulting membership + state proof (public data, no privileged relayer/prover role needed), and submit it via `handle_unsigned` with `address` set to their own account. Because the mint is proportional to `total_bytes` and not to any real relaying cost, and there is no requirement that the submitter be a bona fide relayer or that requests be economically meaningful, this is an unauthenticated, unbounded token-minting primitive against a real fungible asset (`ReputationAsset`) — a direct case of unauthorized value creation/theft-from-protocol via a public entrypoint, not requiring a malicious relayer, prover, or governance actor.

### Likelihood Explanation
High, once `MintPerByte` is non-zero (governance-configurable, and documented as the intended incentive mechanism for this pallet). The path requires no special role: `ensure_none` origin, a legitimate/verifiable proof (which the attacker can trivially manufacture by dispatching their own `GetRequest`), and setting `address` to themselves. No signature check exists to stop it.

### Recommendation
Bind `address` to a verified signer the same way `pallet-messaging-incentives::relayer_for` does: require `GetRequestsWithProof` to carry a signature over the batch (or the same `Message.signer` convention used elsewhere) and recover the reward beneficiary from that signature instead of trusting the raw `address` field. Alternatively, remove independent minting from `pallet-state-coprocessor` and route all reputation minting exclusively through `pallet-messaging-incentives::FeeHandler::on_executed`, which already enforces signature-based attribution.

### Proof of Concept
1. Attacker deploys/uses a contract on any connected source chain and issues a trivial `GetRequest` via the ISMP dispatcher for a storage slot they control (any cheap, publicly-readable key).
2. Attacker (or anyone) collects the resulting membership proof (`source`) and state proof (`response`) from public chain data — no relayer credentials needed.
3. Attacker submits an unsigned `state_coprocessor::handle_unsigned(GetRequestsWithProof { requests, source, response, address: <attacker's own 32 bytes> })` transaction directly to the node's transaction pool.
4. `handle_get_requests` verifies the (legitimate) proofs, computes `total_bytes`, and mints `rate * total_bytes` of `ReputationAsset` to `attacker's own 32 bytes` [8](#0-7) , with `pays_fee`/gas cost far below the value of the minted reputation asset if `MintPerByte` is set to any economically meaningful rate.
5. Repeat indefinitely with new trivial GetRequests to mint `ReputationAsset` at will, since nothing ties `address` to actual relaying work performed.

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L137-153)
```rust
	/// Recover the relayer's account from the sr25519 signature on a
	/// `Message`'s `signer` field. Returns `None` if the message has
	/// no signer (e.g. consensus messages) or the signature is bad.
	fn relayer_for(message: &Message) -> Option<T::AccountId> {
		let (signer, signed) = match message {
			Message::Request(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			Message::Response(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			_ => return None,
		};
		Signature::decode(&mut &signer[..])
			.ok()?
			.verify_and_get_sr25519_pubkey(&signed, None)
			.ok()
			.map(T::AccountId::from)
	}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L53-54)
```rust
	/// Address that should be credited with fees
	pub address: Vec<u8>,
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

**File:** modules/pallets/state-coprocessor/src/lib.rs (L121-148)
```rust
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

**File:** modules/pallets/relayer/src/outbound_request.rs (L168-173)
```rust

		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```
