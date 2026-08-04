This is exactly the analog of the TAU bug: an attacker-supplied identity field decoupled from the actual work/authorization performed, letting the caller redirect a protocol credit to an arbitrary account.

### Title
Unauthenticated `address` field in `handle_get_requests` lets any caller mint relayer reputation to an arbitrary account - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`pallet-state-coprocessor::handle_get_requests` accepts a caller-supplied `GetRequestsWithProof.address` field and, after verifying the state/membership proofs for the batch of `GetResponse`s, mints reputation tokens proportional to `total_bytes` directly to whatever `T::AccountId` is derived from that field [1](#0-0) . Just like the Taurus TAU bug — where `_decreaseCurrentMinted` read `currentMinted[account]` (an attacker/caller-controlled parameter) instead of the entity that actually did the work (`msg.sender`) — this pallet credits reputation based on a field that has no cryptographic or provenance link to who actually supplied/relayed the state proof.

### Finding Description
The extrinsic body is:
```rust
pub struct GetRequestsWithProof {
    pub requests: Vec<GetRequest>,
    pub source: Proof,
    pub response: Proof,
    pub address: Vec<u8>,   // "Address that should be credited with fees"
}
``` [1](#0-0) 

The proof-verification pipeline (membership proof on source, state proof on destination) only proves that the `GetRequest`s were committed on the source chain and that the returned values are correct at the destination — it says nothing about who submitted this extrinsic or who relayed the underlying messages [2](#0-1) .

After computing `total_bytes` (the size metered by the bandwidth gate), the pallet mints reputation directly to the account derived from the untrusted `address` bytes:
```rust
if let Ok(bytes32) = <[u8; 32]>::try_from(address.as_slice()) {
    let relayer: T::AccountId = bytes32.into();
    ...
    <T as pallet_messaging_incentives::Config>::ReputationAsset::mint_into(&relayer, amount)
    ...
}
``` [3](#0-2) 

There is no check that `address` corresponds to `ensure_signed(origin)` of the extrinsic, nor to any relayer identity recovered from a signature over the delivered request/response (the way `pallet-relayer`'s `decode_receipt_relayer` + signature-recovery pattern authenticates delivery attribution elsewhere in this same codebase, e.g. `outbound_consensus.rs` and `outbound_request.rs`) [4](#0-3) . The comment "Mint reputation tokens to the named relayer. The address is the relayer's raw 32-byte public key as supplied by the coprocessor" confirms `address` is simply taken at face value [5](#0-4) .

This mirrors the TAU root cause precisely: `_decreaseCurrentMinted(account, amount)` trusted a passed-in `account` parameter to update `currentMinted[msg.sender]`'s accounting instead of validating that `account == msg.sender`; here, `handle_get_requests` trusts a passed-in `address` parameter to decide who is credited, with no binding to the actual submitter or delivering relayer.

### Impact Explanation
Any account able to assemble valid Hyperbridge state/membership proofs for a batch of `GetResponse`s (which itself requires no special privilege — proofs are public/derivable from chain state, and this is an unsigned/permissionless dispatch path per the module design) can set `address` to any arbitrary account — including their own alt account, a sybil, or a competitor's account to grief them out of expected credit — and receive `ReputationAsset::mint_into` credit that should only go to the relayer that actually did the bandwidth-metered work. This directly causes wrong-beneficiary allocation of a protocol-native incentive asset (`pallet_messaging_incentives::ReputationAsset`), i.e., unauthorized/duplicated minting to the wrong party, falling squarely under "logic attacks" / "false proof-driven fund/asset misallocation" in the bounty scope.

### Likelihood Explanation
High. No relayer/prover/admin compromise is needed — an ordinary user who can construct or replay the (public) proof data for a batch of GetResponses can freely choose the `address` field and repeatedly mint reputation to themselves, whether or not they actually delivered the underlying messages, as long as `MintPerByte` is non-zero. The check is a pure parameter with `try_from(...).is_ok()` gate and silently no-ops on failure rather than rejecting the whole call, so the attack surface is only bounded by the batch's `total_bytes`.

### Recommendation
Bind `address` to a verified identity rather than accepting it as free-form input:
- Require `ensure_signed(origin)` for `handle_get_requests` and use the signing account as the reputation recipient, or
- Require the caller to provide a signature (analogous to `outbound_request_delivery_message` / `decode_receipt_relayer` in `pallet-relayer`) recovered from data that ties back to the actual delivery of the `GetResponse`s, and mint only to the recovered signer, or
- At minimum, verify `address` against an on-chain relayer registration/attestation rather than trusting the raw bytes supplied in the call.

### Proof of Concept
1. Attacker observes (or independently reconstructs) a valid `GetRequestsWithProof` payload for a batch of `GetResponse`s that some other relayer legitimately delivered/could deliver (source membership proof + destination state proof are derivable from public chain data, not secret).
2. Attacker submits `handle_get_requests` with `requests`, `source`, `response` unchanged, but sets `address` to their own 32-byte account id instead of the actual relayer's.
3. Proof verification succeeds (it only certifies the GetResponse values, not who is claiming the reward) [2](#0-1) .
4. `ReputationAsset::mint_into(&attacker_account, amount)` executes, crediting the attacker with reputation proportional to `total_bytes`, regardless of who relayed the request [6](#0-5) .

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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L111-155)
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
			total_bytes = total_bytes.saturating_add(bytes);

			responses.push(response);
		}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L156-184)
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
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L164-173)
```rust
			.get(&receipt_key)
			.cloned()
			.flatten()
			.ok_or(Error::<T>::OutboundDeliveryNotProven)?;

		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```
