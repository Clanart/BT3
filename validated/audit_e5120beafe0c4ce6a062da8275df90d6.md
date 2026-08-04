## Analysis

The RaptorCast report's core broken invariant: **an identity field embedded in a message payload is trusted for privileged effects without ever being checked against a cryptographic signer/authenticator**, letting anyone claim someone else's identity for free.

Hyperbridge has the correct pattern in one place and the broken pattern in a sibling place, which makes the contrast provable from local code.

**Correct pattern** — `pallet-messaging-incentives` recovers the relayer identity from the sr25519 signature on the message's `signer` field before minting reputation: [1](#0-0) 

**Broken pattern** — `pallet-state-coprocessor::GetRequestsWithProof` carries a raw, attacker-supplied `address: Vec<u8>` field described as "Address that should be credited with fees": [2](#0-1) 

This `address` is never checked against any signature, origin, or recovered pubkey. It is used directly to (a) mint `ReputationAsset` and (b) become the recorded relayer on the response receipt/event: [3](#0-2) 

Crucially, the dispatchable that carries this payload is **unsigned** — `ensure_none(origin)` — so there is no account origin to even compare against, and `validate_unsigned` only dedups by message-hash, performing no authentication of `address` either: [4](#0-3) 

### Title
Unauthenticated `address` field in `GetRequestsWithProof` lets anyone steal relayer reputation-mint rewards and the response-relayer attribution — (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`pallet-state-coprocessor::handle_get_requests` (reached via the unsigned extrinsic `handle_unsigned`) accepts a caller-supplied `address: Vec<u8>` that names the beneficiary of reputation-asset minting and the relayer recorded on the response receipt. Unlike the sibling `pallet-messaging-incentives`, which derives the relayer identity by recovering the sr25519 signer from the message's cryptographic signature, this pallet never authenticates `address` against anything — not a signature, not the extrinsic origin (which doesn't exist, since the call is `ensure_none`).

### Finding Description
`GetRequestsWithProof` is a wire-format struct whose `requests`, `source` proof, and `response` proof are all cryptographically verified against Hyperbridge's stored state commitments [5](#0-4) , but the `address` field that determines who gets paid is not part of any signed or proven data — it is decoded straight off the wire and trusted as-is: [6](#0-5) 

Because the call site is `handle_unsigned` with `ensure_none(origin)`, there is no signed account to cross-check `address` against, and `validate_unsigned` only computes a dedup tag over the request hashes — it re-runs `handle_get_requests` for validation but performs no identity binding on `address`: [7](#0-6) 

The `GetRequest`s and their source/response state proofs are proving *publicly observable on-chain state* (the request existed on the source chain, and the queried key had some value on the destination chain at a given height) — anything an attacker can read from public RPCs, not anything requiring possession of a private key or actual relaying infrastructure. This means any address can be inserted as the "delivering relayer" by simply re-submitting the same publicly verifiable proof set with a different `address`, exactly mirroring the RaptorCast pattern of accepting an unverified identity claim embedded in an otherwise-legitimate, well-formed message.

### Impact Explanation
This directly moves value to the wrong beneficiary: `ReputationAsset::mint_into(&relayer, amount)` credits an attacker-chosen account instead of whoever actually incurred the cost of querying and relaying the state proof [8](#0-7) . It also poisons the on-chain relayer attribution recorded in `store_response_receipt`/`GetRequestHandled` events [9](#0-8) , which is exactly the kind of "reward decoupled from the party that did the work" issue the Hyperbridge Impact Gate targets (relayer rewards must move exactly once and only to the rightful beneficiary).

### Likelihood Explanation
High. The call is unsigned and open to anyone who can construct a `GetRequestsWithProof` with a valid, already-public state proof (no privileged key, node, or relayer role required) and set `pays_fee = Pays::No`/no origin check gates it. The only friction is producing a syntactically valid membership/state proof, which any observer of the source/destination chains can copy from already-delivered messages or construct themselves from public state — no malicious relayer/prover/admin assumption needed, satisfying the "unprivileged attacker" bar.

### Recommendation
Bind `address` to a verifiable identity before using it for minting or receipt attribution: either require the call to be signed and use the extrinsic's own origin as the beneficiary (dropping the free-form `address` field entirely), or require `address` to be the sr25519/ecdsa signer recovered from a signature over the batch (the same construction `pallet-messaging-incentives` already uses for its `signer` field). At minimum, reject `handle_unsigned` submissions where `address` cannot be tied to a signature covering the submitted proof set.

### Proof of Concept
1. Observer watches Hyperbridge / source & destination chains for any legitimately dispatched `GetRequest` plus its already-finalized state proof (all public data — no privileged access needed).
2. Attacker constructs `GetRequestsWithProof { requests, source, response, address: <attacker_account> }` using that public data.
3. Attacker submits `state_coprocessor::handle_unsigned(origin=None, message)`. `ensure_none(origin)` passes trivially; `validate_unsigned` only checks the request-hash dedup tag and re-executes `handle_get_requests`, which never checks `address` against anything [7](#0-6) .
4. `handle_get_requests` runs the legitimate proof verification (passes, since the proofs are real), then mints `ReputationAsset` to `attacker_account` and records `attacker_account` as the relayer of the response [6](#0-5) .
5. If a genuine relayer had also submitted (or later submits) an equivalent batch for the same requests, the duplicate-response dedup (`response_receipt` check) means only the first submitter — potentially the attacker — collects the mint and attribution, at zero infrastructure cost to the attacker beyond copying public proof data.

### Citations

**File:** modules/pallets/messaging-incentives/README.md (L24-26)
```markdown
   - Recovers the relayer's account from the sr25519 signature on the message's
     `signer` field.
   - Mints that amount of `ReputationAsset` to the relayer.
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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L111-124)
```rust
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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-234)
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

**File:** modules/pallets/state-coprocessor/src/lib.rs (L90-149)
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
	}
```
