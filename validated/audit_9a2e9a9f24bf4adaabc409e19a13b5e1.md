### Title
Cross-function signature replay: a relayer's `accumulate_fees` beneficiary-redirect signature is byte-identical to, and thus reusable as, a `withdraw_fees` authorization, allowing a third party to drain the relayer's entire accrued fee balance - ([File: modules/pallets/relayer/src/withdrawal.rs], [File: modules/pallets/relayer/src/accumulate.rs])

### Summary
The external report's core broken invariant is: a signed authorization is a public, replayable bearer object, and if the same signature bytes can be validly consumed by more than one on-chain action, an unprivileged third party — not the signer — gets to decide which action executes and with what parameters. In `pallet-ismp-relayer`, the signed payload for `accumulate_fees`'s optional beneficiary redirect and the signed payload for `withdraw_fees` are constructed as SCALE-encoded tuples of identical shape and share the same per-`(address, state_machine)` nonce counter. A signature the relayer creates to redirect one small batch of newly accumulated fees is therefore also a valid signature for a full `withdraw_fees` call that drains the relayer's *entire* currently accrued balance to the same beneficiary — an operation the relayer never authorized at that amount.

### Finding Description
`withdrawal::message` (used by `Pallet::withdraw`) and `accumulate::beneficiary_message` (used by `Pallet::accumulate`'s beneficiary-redirect branch) hash the exact same tuple shape when a beneficiary is present: [1](#0-0) [2](#0-1) 

Both functions hash `(nonce: u64, StateMachine, beneficiary bytes).encode()`. SCALE encoding of `Vec<u8>` and `&[u8]` is identical (compact length prefix + raw bytes), so `message(nonce, dest_chain, Some(beneficiary))` and `beneficiary_message(nonce, state_machine, beneficiary)` produce the **same keccak256 digest** whenever `dest_chain == state_machine` and the beneficiary bytes match — which an attacker fully controls when constructing the competing call.

Crucially, both signature checks read from the *same* nonce storage: [3](#0-2) 

`Pallet::withdraw` verifies against this shared nonce and, on success, pays out the entire live `Fees` balance (not any batch-specific amount) to the attacker-supplied beneficiary, then zeroes the balance: [4](#0-3) [5](#0-4) 

`Pallet::accumulate`'s beneficiary branch verifies the very same message shape against the same nonce, only intending to redirect the fee from the current small proof batch: [6](#0-5) 

Because both extrinsics are unsigned (`ensure_none`) and thus permissionless to submit by any account, and because the signed message is visible the moment it appears in a `WithdrawalInputData`/`WithdrawalProof` payload broadcast to the network — exactly the "signatures are public once observable" primitive from the seed report — any observer can take a beneficiary-redirect signature meant for `accumulate_fees` and instead submit it as a `withdraw_fees` call before the relayer's intended `accumulate_fees` transaction lands.

### Impact Explanation
This is not merely a DoS/griefing scenario (which would be excluded). It is a genuine amount/scope violation: the relayer's signature, scoped by them to authorize crediting a specific (usually small) batch fee to `beneficiary`, gets reinterpreted by a third party as authorization to withdraw the relayer's **entire currently accrued fee balance** (which can be orders of magnitude larger, since fees accumulate continuously from many prior deliveries) to that same beneficiary — while also consuming the nonce, causing the relayer's actually-intended `accumulate_fees` call to subsequently fail. This is a direct violation of the required invariant that "relayer rewards ... must move exactly once and only to the rightful beneficiary and amount": here the amount moved is uncontrolled by the signer and dictated by whichever unprivileged party races the two colliding interpretations of one signature. In the worst case, the entire relayer fee treasury for a chain can be forced out at the moment a much smaller redirect was intended, and — since `withdraw_fees` accepts any `beneficiary` matching what was signed — this always pays to the address the leaked signature names, meaning any actor who can observe the pending signature effectively controls the timing and finality of a full-balance payout the relayer did not intend.

### Likelihood Explanation
This triggers only when a relayer actually uses the optional `beneficiary_details` redirect feature of `accumulate_fees` (an intentionally supported, and documented, code path). Once used, exploitation requires no privileged access, no malicious relayer/prover, and no compromised keys — only observing a publicly broadcast unsigned extrinsic and submitting a competing extrinsic with a matching `dest_chain`/`beneficiary`, which is unsigned itself and requires no special permission (`ensure_none`). This mirrors exactly the "monitor mempool for signatures, then front-run to reuse them" mechanic from the seed report, but here the two consumers are different call sites with a real amount mismatch rather than a pure ordering/DoS race.

### Recommendation
Domain-separate the two signed payloads so a signature for one action can never validate for the other:
- Include a distinct purpose tag/discriminant (e.g. a fixed prefix byte or string such as `b"ISMP-RLYR-WITHDRAW"` vs `b"ISMP-RLYR-ACC-REDIRECT"`) inside both `message()` and `beneficiary_message()` before hashing.
- Additionally bind the accumulate-side signature to the specific batch it authorizes (e.g. include a hash of `withdrawal_proof.commitments` or the resulting `total_fee`) so it cannot be reinterpreted as an unrelated, unrelated-amount withdrawal authorization.
- Consider separate nonce namespaces per action type rather than sharing one `Nonce<T>` counter between `withdraw` and `accumulate`.

### Proof of Concept
1. Relayer `R` has accrued `Fees::<T>::get(SM, R) = 50_000` (BRIDGE) from many prior deliveries on state machine `SM`, with current `Nonce::<T>::get(R, SM) = n`.
2. `R` finishes proving a small new delivery batch worth `20` and wants to redirect just that credit to payout address `B`. It signs `beneficiary_message(n, SM, B) = keccak256((n, SM, B).encode())` and submits `accumulate_fees(withdrawal_proof)` with `beneficiary_details = Some((B, sig))`.
3. Because this is an unsigned extrinsic, `sig`, `n`, `SM`, and `B` are all visible in the pending transaction before inclusion.
4. An unprivileged observer immediately submits `withdraw_fees(WithdrawalInputData{ signature: sig, dest_chain: SM, beneficiary: Some(B) })`.
5. `Pallet::withdraw` computes `msg = message(n, SM, Some(B)) = keccak256((n, SM, B).encode())` — bit-identical to what `R` signed — so `signature.verify(&msg, ..)` succeeds and recovers `R`'s address (`modules/pallets/relayer/src/withdrawal.rs:88-115`).
6. The call reads `available_amount = Fees::<T>::get(SM, R) = 50_000` (the full balance, not the `20` batch amount), dispatches the withdrawal request paying `50_000` to `B`, zeroes `Fees::<T>[SM][R]`, and increments the nonce (`modules/pallets/relayer/src/withdrawal.rs:116-186`).
7. `R`'s originally submitted `accumulate_fees` call now fails signature/nonce checks once it lands, having already had its authorization hijacked into a full-balance withdrawal it never intended at that scope.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-177)
```rust
	pub fn withdraw(withdrawal_data: WithdrawalInputData) -> DispatchResult {
		let address = match &withdrawal_data.signature {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		};

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

**File:** modules/pallets/relayer/src/accumulate.rs (L106-147)
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
