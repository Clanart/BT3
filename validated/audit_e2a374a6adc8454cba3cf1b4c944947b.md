## Analysis

The external report's core broken invariant: **a value meant to make a signed authorization single-use (a nonce) can be consumed or replayed in an unintended context because the signed payload lacks proper domain separation.** The suggested fix was exactly "enforcing robust domain separators when hashing messages."

I found a structurally identical flaw in `modules/pallets/relayer`, where a single per-`(account, state_machine)` nonce counter is shared by **two different privileged, permissionless extrinsics**, and the signed-message encodings for both extrinsics are byte-identical whenever a beneficiary is supplied.

### Title
Missing domain separation lets a relayer-authorization signature for one fee-movement extrinsic be replayed against the other, consuming the shared nonce out-of-band - (File: `modules/pallets/relayer/src/withdrawal.rs`, `modules/pallets/relayer/src/accumulate.rs`)

### Summary
`pallet_ismp_relayer` maintains one `Nonce<T>` storage map keyed by `(account, StateMachine)`, shared by two unrelated signed operations:
- `withdraw()` in `modules/pallets/relayer/src/withdrawal.rs:81-187`, whose signed payload is `message(nonce, dest_chain, beneficiary: Option<Vec<u8>>)` [1](#0-0) 
- `accumulate()`'s beneficiary-redirect path in `modules/pallets/relayer/src/accumulate.rs:106-147`, whose signed payload is `beneficiary_message(nonce, state_machine, beneficiary: &[u8])` [2](#0-1) 

When `beneficiary` is `Some(...)`, `message()`'s SCALE encoding of `(nonce, dest_chain, beneficiary)` is byte-for-byte identical to `beneficiary_message()`'s encoding of `(nonce, state_machine, beneficiary)` — SCALE-encodes `Vec<u8>` and `&[u8]` the same way, and both tuples have the same field order and types. There is no call-type tag, selector, or other domain separator mixed into either hash.

### Finding Description
Both `accumulate_fees` and `withdraw_fees` are unsigned, permissionless extrinsics (`ensure_none(origin)?`) [3](#0-2) , gated only by `validate_unsigned`, whose only real authorization check is recovering the signer from the fixed-format message and matching it against the on-chain-proven `delivery_address` (accumulate) or the `address` embedded in the `Signature` (withdraw). Both paths:

1. Read the *same* `Nonce::<T>::get(address, state_machine)` value.
2. Build a hash of `(nonce, state_machine, beneficiary…)` with no discriminator identifying "this is a withdraw signature" vs "this is an accumulate-redirect signature".
3. On success, increment the same `Nonce<T>` entry.

Because the two hash pre-images collide when a beneficiary is present, a signature a relayer produces and broadcasts intending to invoke one extrinsic is cryptographically valid for the other extrinsic as well, as long as the shared nonce has not yet advanced. Whichever transaction lands first (`accumulate_fees` with `beneficiary_details` or `withdraw_fees`) consumes the nonce and moves `Fees[state_machine][delivery_address]` to `beneficiary_address`; the second transaction — the one the relayer actually meant to execute — now fails signature/nonce verification and reverts, since `Nonce<T>` has already advanced past the value the signature was bound to. [4](#0-3) [5](#0-4) 

Existing guards do not stop this: `OutboundRequestsClaimed`/idempotency tags do not apply here (this is a different pallet path); `MixedDeliveryAddressesInBatch` only limits batch composition within one call; and there is no per-call-type domain tag anywhere in `message()` or `beneficiary_message()`. The corrupted value is the **message digest itself** — it fails to bind "which extrinsic/purpose this signature authorizes," so the shared `Nonce<T>` state can be advanced by the wrong call.

### Impact Explanation
This is a logic/replay flaw across two fund-movement code paths sharing one nonce space with colliding signed-message encodings — exactly the bug class flagged by the external report (nonce state shared/consumed unexpectedly, enabling authorization reuse, mitigated only by adding domain separators). The practical effect is that a relayer's own broadcast, unsigned extrinsic (which necessarily carries its authorizing signature in cleartext on the public transaction pool, since these are `ensure_none` calls) can be resubmitted through the sibling extrinsic before the original lands, consuming the nonce prematurely and diverting the underlying `withdraw`/`accumulate`-beneficiary-redirect settlement through a code path other than the one the relayer intended, causing the relayer's originally-submitted transaction to be invalidated (nonce/signature mismatch) — a duplicate-settlement/logic-attack condition on relayer reward funds.

### Likelihood Explanation
The precondition (a beneficiary-bearing signed payload becoming visible before inclusion, since both calls are unsigned and their calldata, including the signature, is public once broadcast) is inherent to how these two extrinsics are designed — no leaked private key, malicious relayer, or governance actor is required; any third party observing the public mempool for either `accumulate_fees` or `withdraw_fees` calls carrying `beneficiary_details`/`beneficiary` can act on it. The root cause (shared nonce space, identical hash pre-images) is unconditional and always present, not a rare configuration.

### Recommendation
Add an explicit domain/call-type separator to both `message()` and `beneficiary_message()` (e.g., a fixed byte tag or the pallet call index) so a signature valid for `withdraw_fees` can never be a valid signature for `accumulate_fees`'s beneficiary-redirect path or vice versa. Consider also splitting the shared `Nonce<T>` map into separate namespaces per call-type, or including the call index inside the signed tuple, mirroring the report's core recommendation of using robust domain separators when hashing signed messages.

### Proof of Concept
1. Relayer `R` accumulates fees for delivery on `StateMachine::Kusama(2000)` and wants to redirect the payout to `beneficiary B`; it signs `beneficiary_message(nonce=N, Kusama(2000), B)` and submits it as `accumulate_fees(withdrawal_proof)` with `beneficiary_details = Some((B, sig))`. [6](#0-5) 
2. Before this transaction is included, an observer copies `sig` from the mempool and submits `withdraw_fees(WithdrawalInputData { signature: sig, dest_chain: Kusama(2000), beneficiary: Some(B) })`. Because `message(N, Kusama(2000), Some(B))` byte-equals `beneficiary_message(N, Kusama(2000), B)`, `sig` verifies successfully against `R`'s address in `withdraw()` too. [7](#0-6) 
3. `withdraw_fees` executes first: it increments `Nonce::<T>::get(R, Kusama(2000))` to `N+1`, zeroes `Fees[Kusama(2000)][R]`, and dispatches the withdrawal to `B`. [8](#0-7) 
4. `R`'s originally intended `accumulate_fees` call, still carrying the same signature bound to nonce `N`, now reverts (nonce/message mismatch against the advanced `Nonce<T>` value), even though it was the transaction `R` actually meant to submit — demonstrating the shared nonce/colliding-digest pair let an unrelated extrinsic consume `R`'s authorization out of context.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-177)
```rust
		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());

		match &withdrawal_data.signature {
			Signature::Evm { address, .. } => {
				let eth_address = withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
				if &eth_address != address {
					Err(Error::<T>::InvalidPublicKey)?
				}
			},
			Signature::Sr25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
			Signature::Ed25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
		};
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
		};

		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};

		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L192-197)
```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L107-147)
```rust
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

			Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| {
				*value += 1;
				Ok::<(), ()>(())
			})
			.map_err(|_: ()| Error::<T>::ErrorCompletingCall)?;

			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L309-315)
```rust
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
```

**File:** modules/pallets/relayer/src/lib.rs (L350-368)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight({1_000_000})]
		pub fn accumulate_fees(
			origin: OriginFor<T>,
			withdrawal_proof: WithdrawalProof,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::accumulate(withdrawal_proof)
		}

		#[pallet::call_index(1)]
		#[pallet::weight({1_000_000})]
		pub fn withdraw_fees(
			origin: OriginFor<T>,
			withdrawal_data: WithdrawalInputData,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::withdraw(withdrawal_data)
		}
```
