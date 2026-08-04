### Title
Unauthenticated `address` field in `handle_get_requests` lets any caller self-mint unlimited `ReputationAsset` reward tokens - ([File: modules/pallets/state-coprocessor/src/impls.rs])

### Summary
The external report's core defect is that a privileged-looking, low-frequency admin action (`DeployFungibleCoinZRC20` → `SetupChainGasCoinAndPool`) calls `MintCoins()` unconditionally, creating token supply that is never balanced by a corresponding burn or debit — breaking the "total supply never grows out of thin air" invariant, and it is reachable by anyone who can trigger the code path. The local analog is `pallet-state-coprocessor::handle_get_requests` in `modules/pallets/state-coprocessor/src/impls.rs`, which mints `ReputationAsset` tokens to a caller-supplied `address` with no cryptographic binding between that address and the entity that actually delivered the proof, and no limit on how many times the batch can be resubmitted with a new address.

### Finding Description
`handle_get_requests` processes a `GetRequestsWithProof { requests, source, response, address }` payload [1](#0-0) . After verifying the source/response state proofs (which only prove that certain `GetRequest`s existed and their values on the destination chain — public, non-secret information that anyone can read and assemble a valid Merkle/trie proof for), the pallet computes `total_bytes` from the response sizes and then mints `ReputationAsset` directly to whatever `address` was passed in the call, with no signature check tying `address` to the actual submitter/relayer: [2](#0-1) 

Contrast this with the sibling pallet `pallet-messaging-incentives`, which mints the *same* `ReputationAsset` for delivered `Request`/`Response` messages but only after cryptographically recovering the relayer's account from a signature over the message payload: [3](#0-2) [4](#0-3) 

In `handle_get_requests`, there is no equivalent `relayer_for`-style signature recovery — `address` is taken as-is from the submitted payload (only length-checked to be 32 bytes) and used as the mint beneficiary. Both pallets share the same `MintPerByte` rate and the same `ReputationAsset::mint_into` sink, so this is not a separate, lower-stakes reward system — it is the identical token supply that `pallet-messaging-incentives` protects with signature verification.

Because the proofs required (`source` and `response` state proofs) only attest to already-finalized, publicly-observable chain state, any party — not just a legitimate relayer — can independently construct a valid `GetRequestsWithProof` for a batch of `GetRequest`s that have already been serviced/committed, and simply set `address` to their own account (or resubmit repeatedly with different addresses) each time to mint reputation tokens. Unlike the corresponding request-delivery reward pipeline (`OutboundRequestsClaimed` idempotency map in `modules/pallets/relayer/src/outbound_request.rs`), nothing in `handle_get_requests` marks a `(requests, address)` combination as already rewarded — the dedup check (`dedup_requests`, `response_receipt`) only prevents inserting a duplicate *response* into the MMR, it does not gate the reputation mint against being claimed multiple times for proofs built from the same underlying request/response data by different callers or repeated submissions.

### Impact Explanation
`ReputationAsset` is minted with real economic weight in this system — it feeds directly into relayer rewards/reputation accounting shared with `pallet-messaging-incentives` and `pallet-collator-manager` (per the wide usage of `ReputationAsset` across `parachain/runtimes/gargantua/src/lib.rs`, `parachain/runtimes/nexus/src/lib.rs`, and `modules/pallets/collator-manager/src/lib.rs`). An attacker able to name themselves as `address` in a `GetRequestsWithProof` submission can mint this asset without performing any of the actual relaying/delivery work the system intends to reward, and can repeat the process to accumulate an unbounded amount, inflating the reputation-token supply and diluting or corrupting the incentive/collator-selection accounting that depends on it. This is the same class of defect as the Zeta report: a mint path that creates value without consuming or destroying any corresponding value elsewhere, executable outside the set of trusted actors the mechanism was designed for.

### Likelihood Explanation
The state/response proofs consumed by `handle_get_requests` are proofs over public chain state (already-committed values on source/destination chains) — they do not require possession of a private relayer key, a governance role, or any privileged capability. Constructing a valid proof merely requires reading finalized state and building the corresponding trie proof, which is feasible for any unprivileged actor with RPC access to the chains involved. Because `address` is copied verbatim into the mint call with only a length check, and there is no idempotency/claim-tracking on the mint step, the path is reachable and repeatable by any external caller, making likelihood high.

### Recommendation
Bind the mint beneficiary to a verified actor the same way `pallet-messaging-incentives::relayer_for` does: require a signature over the request/response payload (or over `(commitment, address)`) from the account being credited, and recover the account from that signature rather than trusting the caller-supplied `address` field directly. Additionally, add an idempotency guard (e.g., a per-commitment/per-batch "already rewarded" storage map, mirroring `OutboundRequestsClaimed` in `modules/pallets/relayer/src/outbound_request.rs`) so a given request/response batch cannot be used to mint reputation more than once.

### Proof of Concept
1. Observe a batch of `GetRequest`s that have already been serviced on the destination chain (public data — no special access needed).
2. Independently reconstruct the `source` proof (that the requests were committed on the source chain) and the `response` proof (state values on the destination chain) using standard trie-proof tooling against the already-finalized state roots.
3. Submit `GetRequestsWithProof { requests, source, response, address: attacker_account_bytes }` to the extrinsic that calls `Pallet::handle_get_requests` (`modules/pallets/state-coprocessor/src/lib.rs`, dispatching into `modules/pallets/state-coprocessor/src/impls.rs:62`).
4. Verification of `source`/`response` proofs succeeds (they are valid proofs of real, public data) at `modules/pallets/state-coprocessor/src/impls.rs:112-124`.
5. The pallet computes `total_bytes` and calls `ReputationAsset::mint_into(&attacker_account, amount)` at `modules/pallets/state-coprocessor/src/impls.rs:171-173`, crediting the attacker with reputation tokens for work they did not perform.
6. Repeat with a fresh `address` (or the same one) using the same or overlapping request/response data — no on-chain state prevents re-minting, since only response-insertion (not the reputation mint) is deduplicated. [5](#0-4) [2](#0-1)

### Citations

**File:** modules/pallets/state-coprocessor/src/impls.rs (L43-55)
```rust
#[derive(
	Debug, Clone, Encode, Decode, DecodeWithMemTracking, PartialEq, Eq, scale_info::TypeInfo,
)]
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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L62-74)
```rust
	pub fn handle_get_requests(
		GetRequestsWithProof { requests, source, response, address }: GetRequestsWithProof,
	) -> Result<(), Error> {
		// 1. Verify source proofs
		// 2. Extract fees
		// 3. Verify response proof
		// 4. insert GetResponse into mmr and request receipts
		// 5. emit Response events
		let host = <<T as Config>::IsmpHost>::default();

		// Reject duplicate requests within the batch.
		let wrapped: Vec<Request> = requests.iter().cloned().map(Request::Get).collect();
		dedup_requests::<<T as Config>::IsmpHost>(&wrapped)?;
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

**File:** modules/pallets/messaging-incentives/src/lib.rs (L160-187)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		_events: Vec<IsmpEvent>,
	) -> DispatchResultWithPostInfo {
		let rate = MintPerByte::<T>::get();
		if !rate.is_zero() {
			for mw in &messages {
				let bytes = Self::message_bytes(&mw.message);
				let bytes_balance: BalanceOf<T> = (bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if amount.is_zero() {
					continue;
				}
				if let Some(relayer) = Self::relayer_for(&mw.message) {
					match T::ReputationAsset::mint_into(&relayer, amount) {
						Ok(_) =>
							Self::deposit_event(Event::ReputationMinted { relayer, bytes, amount }),
						Err(err) => log::warn!(
							target: "messaging-incentives",
							"reputation mint failed for {bytes}b: {err:?}",
						),
					}
				}
			}
		}
		Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
	}
}
```
