Confirmed: `Nonce<T>` is a single `StorageDoubleMap<Vec<u8>, StateMachine, u64>` shared by both `withdrawal::message()` and `accumulate::beneficiary_message()`, and both signed payloads hash the identical tuple shape `(u64, StateMachine, Vec<u8>)` with no domain-separation tag or discriminant distinguishing "withdraw beneficiary redirect" from "accumulate beneficiary redirect".

### Title
Missing domain separation between `withdraw_fees` and `accumulate_fees` beneficiary signatures lets a relayer's redirect signature be replayed across call types to divert fees - (File: modules/pallets/relayer/src/withdrawal.rs, modules/pallets/relayer/src/accumulate.rs)

### Summary
`pallet-ismp-relayer` computes two different signed messages — `withdrawal::message(nonce, dest_chain, beneficiary)` and `accumulate::beneficiary_message(nonce, state_machine, beneficiary)` — that both hash the exact same tuple shape `(u64, StateMachine, Vec<u8>)` over the same shared `Nonce<T>` counter keyed by `(address, StateMachine)`.

### Finding Description
`Pallet::withdraw` signs/verifies `message(nonce, dest_chain, Some(beneficiary))` [1](#0-0) , while `Pallet::accumulate` signs/verifies `beneficiary_message(nonce, state_machine, &beneficiary_address)` for redirecting accumulated fees to a different beneficiary [2](#0-1) . Both read/increment the identical `Nonce<T>` storage double-map keyed by `(address_bytes, StateMachine)` [3](#0-2) , and both hash functions SCALE-encode the same field order `(u64, StateMachine, Vec<u8>)` with no type/action discriminant byte to separate the domains. Because `Encode` for this tuple shape produces byte-identical output regardless of which call the relayer intended, a signature a relayer produces for a `withdraw_fees(beneficiary=X)` call at nonce N on chain C is also a valid signature for `accumulate_fees`'s beneficiary-redirect at the same nonce N on the same chain C (and vice versa), since `verify()` only checks the raw message hash against the recovered/declared address [4](#0-3) .

### Impact Explanation
Both call paths consume the nonce and, if the corresponding proof/verification steps pass, redirect fee custody to the signed beneficiary — `withdraw` dispatches `available_amount` to `beneficiary_address` [5](#0-4) , and `accumulate` credits `Fees::<T>` under the signed `beneficiary_address` instead of the delivering relayer [6](#0-5) . If an attacker observes a relayer's `withdraw_fees` beneficiary signature on-chain (unsigned extrinsics are public in the mempool/block), they can extract the raw signature bytes and beneficiary bytes and resubmit them as the `beneficiary_details` field of an `accumulate_fees` call for the same relayer/chain/nonce, causing subsequently accumulated fees to be attributed to the attacker-controlled beneficiary instead of the relayer, or vice versa (splicing an old accumulate-redirect signature into a withdrawal). This is a fund-diversion / wrong-beneficiary vulnerability reachable by any unprivileged actor submitting an unsigned extrinsic once a legitimate signature of the correct shape has been observed on-chain.

### Likelihood Explanation
Both extrinsics are `ensure_none` (unsigned, publicly submittable) [7](#0-6) , and relayer signatures are exposed in plaintext within submitted `WithdrawalInputData`/`WithdrawalProof` payloads, making extraction trivial for any chain observer. Exploitation only requires that a relayer has, at some point, produced a beneficiary-redirect signature for a given `(address, chain, nonce)`; the attacker resubmits it against the sibling call before the relayer's next legitimate use of that nonce.

### Recommendation
Add an explicit domain-separation discriminant (e.g., a fixed context byte/string such as `b"RELAYER_WITHDRAW"` vs `b"RELAYER_ACCUMULATE_BENEFICIARY"`) into both `message()` and `beneficiary_message()` before hashing, or use disjoint nonce counters/storage maps for the two call types so a signature produced for one action can never validate for the other.

### Proof of Concept
1. Relayer `R` calls `withdraw_fees` on chain `C`, signing `message(nonce=N, C, Some(beneficiary=B))`; this becomes public in the submitted extrinsic. [8](#0-7) 
2. Before `R`'s nonce for `(R, C)` advances again through the `accumulate` beneficiary path (nonce maps are shared per `(address, StateMachine)`), attacker extracts `R`'s signature and `B` bytes from the on-chain call data.
3. Attacker submits `accumulate_fees` with `beneficiary_details = Some((B, signature))` for a delivery batch attributed to `R` on chain `C`; `Nonce::<T>::get(&R, C)` still equals `N`, so `beneficiary_message(N, C, B)` — byte-identical to the earlier `message(N, C, Some(B))` — verifies successfully against `R`'s signature. [9](#0-8) 
4. The freshly accumulated fee is credited to `B` instead of `R`, redirecting funds without `R`'s intent for this specific accumulation event. [10](#0-9)

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-115)
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
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L133-158)
```rust
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

**File:** modules/pallets/relayer/src/accumulate.rs (L110-126)
```rust
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

**File:** modules/pallets/relayer/src/accumulate.rs (L128-139)
```rust
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

**File:** modules/pallets/relayer/src/lib.rs (L124-135)
```rust
	/// Latest nonce for each address and the state machine they want to withdraw from
	#[pallet::storage]
	#[pallet::getter(fn nonce)]
	pub type Nonce<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		Vec<u8>,
		Blake2_128Concat,
		StateMachine,
		u64,
		ValueQuery,
	>;
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
