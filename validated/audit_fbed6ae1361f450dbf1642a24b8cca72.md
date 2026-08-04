Based on my research, I found a direct local analog to the missing-address-validation bug class in `pallet-ismp-relayer`'s fee accumulation/withdrawal flow.

### Title
Unvalidated `beneficiary_address` length/format in `accumulate()` permanently locks relayer fees - (File: `modules/pallets/relayer/src/accumulate.rs`)

### Summary
`Pallet::accumulate` (called from the unsigned extrinsic `accumulate_fees`) accepts an optional `beneficiary_details: (Vec<u8>, Signature)` that redirects accrued relayer fees to an arbitrary-length `beneficiary_address` byte vector, with no validation that the bytes are a well-formed address for the target `state_machine` (20 bytes for EVM, 32 bytes for Substrate). This mirrors the reported bug class exactly: a signed operation whose address-carrying field is never format-checked at the point of acceptance, only later at a different code path that actually needs the well-formed value.

### Finding Description
In `accumulate()`, when `beneficiary_details` is present, the pallet verifies only that the *signature* over `beneficiary_message(nonce, state_machine, &beneficiary_address)` recovers to the proven `delivery_address` — it never checks that `beneficiary_address` itself is a valid address for `state_machine`: [1](#0-0) 

The value is then written straight into the `Fees<T>` double-map as the storage key: [2](#0-1) 

Contrast this with `withdraw()` in `withdrawal.rs`, which is the *only* place format is finally checked — and only per destination-chain branch, well after the malformed value has already been persisted:
- Substrate destination requires exactly 32 bytes (`AccountId32::try_from`)
- EVM destination requires exactly 20 bytes (enforced deep inside `WithdrawalParams::abi_encode` → `TryFrom<&WithdrawalParams>`) [3](#0-2) [4](#0-3) 

Because `Fees<T>` is keyed by the raw, unvalidated `Vec<u8>` (`Blake2_128Concat, Vec<u8>` in the storage definition), any length/format other than what `withdraw()` expects for that `state_machine` means the accumulated balance is stored under a key that can never again satisfy the length checks in `withdraw()` — the funds become permanently unwithdrawable. [5](#0-4) 

### Impact Explanation
Fees legitimately earned by a relayer for proven message delivery are moved into an address bucket with no format guarantee, and the only later check (in `withdraw`) rejects malformed lengths rather than recovering the funds. This is a straight loss-of-funds condition on the relayer-rewards fund path — the same "no ValidateBasic-style checking on the address parameter, so a downstream operation fails" class as the external report, just realized here as fund lock instead of a reverted swap.

### Likelihood Explanation
Reaching `accumulate_fees` requires no privileged role — it is dispatched via `ensure_none(origin)` and is only spam-guarded by `validate_unsigned`. The redirect path does require a valid signature from the delivering relayer's own key over `(nonce, state_machine, beneficiary_address)`, so exploiting this against a *different* victim's funds is not possible without that relayer's key. What is concretely, locally provable without any extra trust assumption is that the protocol itself provides zero defense-in-depth against a malformed `beneficiary_address` being accepted and persisted at accumulation time — any tooling bug, encoding mismatch, or off-by-length error on the signer's side silently and irreversibly locks the relayer's own fees, with no error raised until it's too late to correct (the earlier proof-and-signature verification succeeds; only the later chain-specific withdrawal decode fails).

### Recommendation
Validate `beneficiary_address` length/format against `state_machine` (20 bytes for EVM destinations, 32 bytes for Substrate destinations, matching exactly what `withdraw()` requires) inside `accumulate()` before it is used to key into `Fees<T>`, rejecting the extrinsic with a new `Error::InvalidBeneficiaryAddress` otherwise — the same fix pattern the report recommends for `MsgUpdateBrokerAddress.ValidateBasic`.

### Proof of Concept
1. A relayer proves delivery of a batch of requests via `accumulate_fees(withdrawal_proof)` for a `state_machine` that `is_evm()`.
2. They include `beneficiary_details = Some((beneficiary_address, signature))` where `beneficiary_address` is, e.g., 5 bytes (or 32 bytes instead of the required 20) and `signature` correctly signs `beneficiary_message(nonce, state_machine, &beneficiary_address)` with their own key (`eth_address == delivery_address` passes at line 116 of `accumulate.rs`).
3. `accumulate()` proceeds: `Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), ...)` credits the fee under the malformed 5-byte key.
4. The relayer later calls `withdraw_fees` — but `withdraw()` derives its own lookup `address` strictly from the signature (`Signature::Evm{address,..}` = 20 bytes), which will never equal the malformed 5-byte key stored in `Fees`. `Fees::<T>::get(dest_chain, address)` returns `0` / the wrong entry, and the accumulated amount under the malformed key is permanently unreachable — no extrinsic path exists to correct or reclaim it.

**Caveat:** I could not find any path in this repository allowing an attacker to redirect *another* relayer's fees to a malformed address without that relayer's own signing key — the confirmed impact here is a self-inflicted, irreversible fund lock rather than third-party fund theft. I'm flagging this explicitly since the bounty gate strongly prefers attacker-controlled loss to a victim; this finding is the closest concrete, evidence-backed analog to the reported bug class but its blast radius is narrower than "unauthorized theft."

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L106-139)
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

**File:** evm/rust/src/host_params.rs (L242-260)
```rust
impl TryFrom<&WithdrawalParams> for WithdrawParamsAbi {
	type Error = WithdrawalParamsError;

	fn try_from(value: &WithdrawalParams) -> Result<Self, Self::Error> {
		// Reject anything that isn't exactly a 20-byte EVM address.
		if value.beneficiary_address.len() != 20 {
			Err(WithdrawalParamsError::InvalidBeneficiaryAddressLength(
				value.beneficiary_address.len(),
			))?;
		}
		let beneficiary = H160::from_slice(&value.beneficiary_address[..]);

		Ok(WithdrawParamsAbi {
			beneficiary: beneficiary.0.into(),
			amount: alloy_primitives::U256::from_be_bytes(value.amount.to_big_endian()),
			token: value.token.0.into(),
		})
	}
}
```

**File:** modules/pallets/relayer/src/lib.rs (L111-122)
```rust
	/// double map of address to source chain, which holds the amount of the relayer address
	#[pallet::storage]
	#[pallet::getter(fn relayer_fees)]
	pub type Fees<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		U256,
		ValueQuery,
	>;
```
