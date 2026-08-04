## Finding

### Title
Unauthenticated `address` field in `GetRequestsWithProof` lets any unprivileged caller redirect `ReputationMinted` rewards to an arbitrary account - (`modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`Pallet::handle_get_requests` mints reputation tokens to whatever account is encoded in the caller-supplied `address` field of `GetRequestsWithProof`, with no check that `address` corresponds to the origin, a signature, or any relayer registry entry.

### Finding Description
`GetRequestsWithProof` is dispatched through `handle_unsigned`, which uses `ensure_none(origin)` — i.e. there is no signed origin at all, and `validate_unsigned` only re-runs `handle_get_requests` for proof validity, never checking `address`: [1](#0-0) [2](#0-1) 

Inside `handle_get_requests`, after verifying the source membership proof and destination state proof, the code mints reputation to the raw `address` bytes with no binding whatsoever to the extrinsic submitter or to any signature over the request/response payload: [3](#0-2) 

The `address` field itself is documented only as "the relayer's raw 32-byte public key as supplied by the coprocessor" and is accepted verbatim: [4](#0-3) 

This differs sharply from the sibling design in `pallet-relayer`, where a claimed `beneficiary_address` is only honored after a signature over a nonce-bound message is verified against the delivery address recovered from the state proof: [5](#0-4) 

No equivalent signature/identity check exists for `GetRequestsWithProof.address`. The only requirement to submit a valid batch is possessing a valid source-chain membership proof and destination-chain storage proof — both are public artifacts derivable by anyone with RPC access to the two chains, not proof of having performed any privileged relaying role.

### Impact Explanation
Reputation minted via `ReputationMinted` is the core input to collator selection on Hyperbridge (per `docs/content/developers/network/collator.mdx`), and mirrors `$BRIDGE`-equivalent value 1:1. Because `address` is fully attacker-controlled and unauthenticated, any unprivileged party who can construct a valid `(source, response)` proof pair — using only public chain data, without doing any privileged relay work tied to that specific account — can mint reputation to an arbitrary account of their choosing. This lets an attacker inflate their own reputation without performing the corresponding proportional relaying work, or (more narrowly) route rewards to an account unrelated to whoever fetched/relayed the proof, corrupting the "rightful beneficiary" invariant required for relayer rewards.

### Likelihood Explanation
High: the call is unsigned (`ensure_none`), requires no bond, fee, or registry entry, and the proof material needed to construct a valid batch (source membership proof + destination storage proof) is public information obtainable by anyone monitoring the connected chains — no cooperation from, or compromise of, any relayer/operator is required.

### Recommendation
Bind the reward beneficiary to a verifiable identity: either require `handle_unsigned` (or an equivalent signed extrinsic) to use the transaction's signed origin as the reward beneficiary, or require a signature over the batch (as `pallet-relayer` does with `beneficiary_details`) proving control of the `address` claimed, so reputation cannot be minted to an account that did not participate in producing/submitting the proof.

### Proof of Concept
1. Observe a pending `GetRequest` batch's source-chain membership proof and destination-chain storage proof (both public via RPC).
2. Construct `GetRequestsWithProof { requests, source, response, address: <attacker_account_bytes32> }` where `address` is unrelated to any actual relaying infrastructure.
3. Submit via `Pallet::handle_unsigned` (unsigned extrinsic, `ensure_none` origin).
4. Observe `Event::ReputationMinted { relayer: address, bytes, amount }` deposited and `ReputationAsset::mint_into(&relayer, amount)` crediting the attacker-chosen account, confirmed by the code path at [6](#0-5) , with no check anywhere in the function tying `address` to the submitter.

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

**File:** modules/pallets/state-coprocessor/src/lib.rs (L121-129)
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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-184)
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

**File:** modules/pallets/relayer/src/accumulate.rs (L106-126)
```rust
		// Let's verify the beneficiary address
		let beneficiary_address = if let Some((beneficiary_address, signature)) =
			withdrawal_proof.beneficiary_details
		{
			let nonce = Nonce::<T>::get(&delivery_address, state_machine);
			let msg = beneficiary_message(nonce, state_machine, &beneficiary_address);
			match &signature {
				Signature::Evm { .. } => {
					let eth_address =
						signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
					if eth_address != delivery_address {
						Err(Error::<T>::InvalidPublicKey)?
					}
				},
				Signature::Sr25519 { .. } | Signature::Ed25519 { .. } => {
					// verify the signature with the delivery address from the state proof
					let _ = signature
						.verify(&msg, Some(delivery_address.clone()))
						.map_err(|_| Error::<T>::InvalidSignature)?;
				},
			}
```
