I found a concrete local analog to the reported bug class: unbounded, unmetered iteration over an attacker/user-controlled list, priced with a fixed weight, executed on the block-critical path.

### Title
Unbounded `GetRequest.keys` causes unmetered state-proof verification work in `pallet-state-coprocessor::handle_unsigned`, exposing a chain-halt vector - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`GetRequest.keys` is an unbounded `Vec<Vec<u8>>` [1](#0-0) . Any application dispatching a GET request via `DispatchGet` can set an arbitrarily large `keys` vector at essentially fixed dispatch cost [2](#0-1) . When the response for that request is later processed on Hyperbridge via `pallet-state-coprocessor::handle_unsigned`, the pallet iterates every `GetRequest` and, for each, calls `verify_state_proof` over the full, unbounded `keys` list — doing real Merkle/Patricia trie cryptographic verification per key — before any bandwidth/cost enforcement gates the operation [3](#0-2) . The dispatching extrinsic's declared weight is a small fixed constant, `reads_writes(1, 2)`, completely independent of `requests.len()` or the total number of keys [4](#0-3) .

### Finding Description
This mirrors the reported bug class exactly: a cheap, user-controlled action (creating an object with an unbounded list) later triggers unmetered linear iteration during a separate, chain-critical operation, with a declared cost that does not scale with the actual work performed.

- `GetRequest.keys: Vec<Vec<u8>>` has no length bound anywhere in the type or at dispatch time [1](#0-0) .
- `dispatch_request` for `DispatchRequest::Get` simply copies `dispatch_get.keys` into the outgoing `GetRequest` with no size validation [5](#0-4) .
- `pallet-state-coprocessor::handle_get_requests` accepts a batch of such `GetRequest`s (`GetRequestsWithProof.requests: Vec<GetRequest>` — itself also unbounded) and, per request, calls `dest_state_machine.verify_state_proof(&host, req.keys.clone(), ...)`, which performs a full Merkle-Patricia (or EVM storage) trie lookup per key [6](#0-5) [7](#0-6) .
- The bandwidth metering (`BandwidthGate::try_consume`) that is supposed to gate per-app data consumption only runs *after* `verify_state_proof` has already been executed — the comment even states this explicitly: "Charged after proof verification so the value sizes are final" [8](#0-7) . So an app without bandwidth allowance still forces the chain to pay the full cryptographic verification cost before being rejected.
- The extrinsic's `#[pallet::weight]` is `<T as frame_system::Config>::DbWeight::get().reads_writes(1, 2)` — a fixed DB-access weight, not scaled by `requests.len()` or `sum(keys.len())` [9](#0-8) .
- This is unsigned and permissionless: "anyone can execute ISMP messages for free, provided they have valid proofs and the messages have not been previously processed" (the analogous pattern in `pallet_ismp::handle_unsigned` documents the same permissionless model) [10](#0-9) . `pallet-state-coprocessor`'s own `validate_unsigned` re-runs the *entire* `handle_get_requests` (including all trie verification) during transaction-pool validation as well, before the extrinsic is even included in a block [11](#0-10) , doubling the unmetered cost per submission attempt and making it exploitable even without inclusion.

The same "cheap create, expensive process later, fixed declared weight" pattern the external report flags (spam denoms → unmetered iteration during reward calc) is reproduced here as: cheap GetRequest dispatch with a huge `keys` array → unmetered trie-verification iteration during coprocessor response handling, charged a fixed, size-independent weight, invoked via an unsigned/permissionless call that also gets executed redundantly at mempool-validation time.

### Impact Explanation
Because the weight charged to this extrinsic does not scale with the actual verification work, an attacker can craft a `GetRequestsWithProof` containing one or more `GetRequest`s with tens of thousands of keys (each requiring a real trie-proof lookup), causing block execution (and, redundantly, `validate_unsigned` mempool checks) to take far longer than the weight budget assumes. Submitted repeatedly, or with a single sufficiently large batch, this can stall block production/import — a permissionless chain-halt vector, matching the "conditional chain halt vector due to unmetered iteration over unbounded [user-controlled list]" bug class from the seed report. This is a runtime/pallet-level impact directly reachable by an unprivileged actor (any app permitted to dispatch a GET request, or any address that self-relays/submits the resulting `handle_unsigned` call), not requiring a malicious relayer, prover, or governance actor.

### Likelihood Explanation
Moderate-to-low today because per-app bandwidth gating (`pallet-bandwidth`) exists in the system and can restrict which apps' requests reach the coprocessor at all — however, that gate is checked *after* the expensive verification runs, and it can also be bypassed entirely if a chain runs with the `no-bandwidth` feature flag (`NoopBandwidthGate`) [12](#0-11) , or for allowlisted apps [13](#0-12) . Similar to the seed report's assessment, likelihood rises sharply if bandwidth gating is disabled/misconfigured or an app is allowlisted, since nothing else bounds `keys.len()`.

### Recommendation
1. Bound `GetRequest.keys` (and `GetRequestsWithProof.requests`) with an explicit maximum length enforced at dispatch time (`dispatch_request`) and again at `handle_get_requests`/`handle_unsigned` entry, rejecting oversized batches before any proof verification runs.
2. Make the `#[pallet::weight]` for `pallet-state-coprocessor::handle_unsigned` scale with `requests.len()` and total key count (or charge a per-key/per-byte weight component), so the declared cost matches the real verification cost.
3. Move (or duplicate cheaply) a size/key-count check ahead of `verify_state_proof` so the bandwidth gate's "fail fast" property actually applies before expensive cryptography runs, not after.

### Proof of Concept
1. An app dispatches a `DispatchGet` with `keys` containing, e.g., 50,000 32-byte entries, paying only the fixed dispatch weight (`modules/pallets/ismp/src/dispatcher.rs:108-126`) — no key-count limit is enforced.
2. A relayer (or the attacker itself, since the coprocessor call is unsigned/permissionless) submits `GetRequestsWithProof { requests: [that GetRequest], ... }` to `pallet-state-coprocessor::handle_unsigned`.
3. `validate_unsigned` runs `handle_get_requests` in full (including `verify_state_proof` over all 50,000 keys) just to validate the transaction for the pool (`modules/pallets/state-coprocessor/src/lib.rs:121-130`), and it runs again at block-execution time — both charged only the fixed `reads_writes(1,2)` weight.
4. Repeating this with several such batches across the block gas/weight budget causes real wall-clock verification time to vastly exceed the weight accounted for, stalling block production/import — the chain-halt vector.

### Citations

**File:** modules/ismp/core/src/router.rs (L101-128)
```rust
pub struct GetRequest {
	/// The source state machine of this request.
	#[serde(with = "serde_hex_utils::as_string")]
	pub source: StateMachine,
	/// The destination state machine of this request.
	#[serde(with = "serde_hex_utils::as_string")]
	pub dest: StateMachine,
	/// The nonce of this request on the source chain
	pub nonce: u64,
	/// Module identifier of the sending module
	#[serde(with = "serde_hex_utils::as_hex")]
	pub from: Vec<u8>,
	/// Raw Storage keys that would be used to fetch the values from the counterparty
	/// For deriving storage keys for ink contract fields follow the guide in the link below
	/// `<https://use.ink/datastructures/storage-in-metadata#a-full-example>`
	/// Substrate Keys
	/// The algorithms for calculating raw storage keys for different substrate pallet storage
	/// types are described in the following links
	/// `<https://github.com/paritytech/substrate/blob/master/frame/support/src/storage/types/map.rs#L34-L42>`
	/// `<https://github.com/paritytech/substrate/blob/master/frame/support/src/storage/types/double_map.rs#L34-L44>`
	/// `<https://github.com/paritytech/substrate/blob/master/frame/support/src/storage/types/nmap.rs#L39-L48>`
	/// `<https://github.com/paritytech/substrate/blob/master/frame/support/src/storage/types/value.rs#L37>`
	/// EVM Keys
	/// For fetching keys from EVM contracts each key should either be 52 bytes or 20 bytes
	/// For 52 byte keys we expect it to be a concatenation of contract address and slot hash
	/// For 20 bytes we expect it to be a contract or account address
	#[serde(with = "serde_hex_utils::seq_of_hex")]
	pub keys: Vec<Vec<u8>>,
```

**File:** modules/ismp/core/src/dispatcher.rs (L38-53)
```rust
/// Simplified GET request, intended to be used for sending outgoing requests
#[derive(Clone)]
pub struct DispatchGet {
	/// The destination state machine of this request.
	pub dest: StateMachine,
	/// Module identifier of the sending module
	pub from: Vec<u8>,
	/// Raw Storage keys that would be used to fetch the values from the counterparty
	pub keys: Vec<Vec<u8>>,
	/// Height at which to read the state machine.
	pub height: u64,
	/// Some application-specific metadata relating to this request
	pub context: Vec<u8>,
	/// Relative from the current timestamp at which this request expires in seconds.
	pub timeout: u64,
}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L126-151)
```rust
		// Insert GetResponses into mmr
		let mut responses = vec![];
		// Total payload bytes across this batch, used to mint reputation to
		// the relayer named in `address`. Each response contributes its
		// abi-encoded size — the same quantity the bandwidth gate charges —
		// so the mint stays proportional to the work paid for.
		let mut total_bytes: u32 = 0;
		for req in requests {
			let values: Vec<StorageValue> = dest_state_machine
				.verify_state_proof(&host, req.keys.clone(), state_root.state_root, &response)?
				.into_iter()
				.map(|(key, value)| StorageValue { key, value })
				.collect();

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

**File:** modules/pallets/state-coprocessor/src/lib.rs (L121-130)
```rust
		fn validate_unsigned(_source: TransactionSource, call: &Self::Call) -> TransactionValidity {
			let Call::handle_unsigned { message } = call else {
				return Err(TransactionValidityError::Invalid(InvalidTransaction::Call));
			};

			if let Err(err) = Self::handle_get_requests(message.clone()) {
				log::error!(target: "ismp", "{:?}", err);
				return Err(TransactionValidityError::Invalid(InvalidTransaction::Call));
			}

```

**File:** modules/pallets/ismp/src/dispatcher.rs (L108-126)
```rust
		let request = match request {
			DispatchRequest::Get(dispatch_get) => {
				let get = GetRequest {
					source: self.host_state_machine(),
					dest: dispatch_get.dest,
					nonce: self.next_nonce(),
					from: dispatch_get.from,
					keys: dispatch_get.keys,
					height: dispatch_get.height,
					context: dispatch_get.context,
					timeout_timestamp: if dispatch_get.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_get.timeout)
					},
				};
				Request::Get(get)
```

**File:** modules/ismp/state-machines/substrate/src/lib.rs (L240-280)
```rust
	fn verify_state_proof(
		&self,
		_host: &dyn IsmpHost,
		keys: Vec<Vec<u8>>,
		root: H256,
		proof: &Proof,
	) -> Result<BTreeMap<Vec<u8>, Option<Vec<u8>>>, Error> {
		// The trie root is supplied by the caller, bound to the calling context, so a relayer
		// cannot steer verification at the wrong trie.
		let StateMachineProof { hasher, storage_proof } =
			codec::Decode::decode(&mut &*proof.proof)
				.map_err(SubstrateStateMachineError::ProofDecodeError)?;
		let data = match hasher {
			HashAlgorithm::Keccak => {
				let db = StorageProof::new(storage_proof).into_memory_db::<Keccak256>();
				let trie = TrieDBBuilder::<LayoutV0<Keccak256>>::new(&db, &root).build();
				keys.into_iter()
					.map(|key| {
						let value = trie
							.get(&key)
							.map_err(|e| SubstrateStateMachineError::TrieError(format!("{e:?}")))?;
						Ok::<_, SubstrateStateMachineError>((key, value))
					})
					.collect::<Result<BTreeMap<_, _>, _>>()?
			},
			HashAlgorithm::Blake2 => {
				let db = StorageProof::new(storage_proof).into_memory_db::<BlakeTwo256>();
				let trie = TrieDBBuilder::<LayoutV0<BlakeTwo256>>::new(&db, &root).build();
				keys.into_iter()
					.map(|key| {
						let value = trie
							.get(&key)
							.map_err(|e| SubstrateStateMachineError::TrieError(format!("{e:?}")))?;
						Ok::<_, SubstrateStateMachineError>((key, value))
					})
					.collect::<Result<BTreeMap<_, _>, _>>()?
			},
		};

		Ok(data)
	}
```

**File:** modules/pallets/ismp/src/lib.rs (L358-382)
```rust
	#[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Execute the provided batch of ISMP messages, this will short-circuit and revert if any
		/// of the provided messages are invalid. This is an unsigned extrinsic that permits anyone
		/// execute ISMP messages for free, provided they have valid proofs and the messages have
		/// not been previously processed.
		///
		/// The dispatch origin for this call must be an unsigned one.
		///
		/// - `messages`: the messages to handle or process.
		///
		/// Emits different message events based on the Message received if successful.
		#[pallet::weight(weight())]
		#[pallet::call_index(0)]
		#[frame_support::transactional]
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			messages: Vec<Message>,
		) -> DispatchResultWithPostInfo {
			ensure_none(origin)?;

			Self::execute(messages.clone())?;

			Ok(().into())
		}
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L82-98)
```rust
#[cfg(not(feature = "no-bandwidth"))]
type RuntimeBandwidthGate = pallet_bandwidth::Pallet<Runtime>;
#[cfg(feature = "no-bandwidth")]
type RuntimeBandwidthGate = NoopBandwidthGate;

#[cfg(feature = "no-bandwidth")]
pub struct NoopBandwidthGate;
#[cfg(feature = "no-bandwidth")]
impl pallet_bandwidth::BandwidthGate for NoopBandwidthGate {
	fn try_consume(
		_source: &StateMachine,
		_app: &[u8],
		_bytes: u32,
	) -> Result<(), pallet_bandwidth::GateError> {
		Ok(())
	}
}
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L95-102)
```text
### Allowlist bypass

The `Allowlist` storage map is a per-`(source, app)` flag that short-circuits the gate. It exists for two reasons:

- **Phased rollout.** Protocol-sponsored apps (e.g. core token gateways) that haven't yet migrated to the bandwidth model bypass the gate while their integration is in flight.
- **Migration paths.** Apps that need a brief unpaid window during a contract upgrade can be allowlisted and revoked once they're back on the meter.

Allowlist membership is admin-only and emits `AllowlistChanged` on every flip.
```
