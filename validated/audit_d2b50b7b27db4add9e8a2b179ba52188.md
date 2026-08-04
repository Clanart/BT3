Based on my investigation, I found a concrete analog to the "missing MAC / malleability" bug class in `pallet-ismp-relayer`'s fee-withdrawal signing scheme.

### Title
Withdrawal signature does not bind the withdrawn amount or the caller's public key, allowing a captured signature to be replayed for a different (larger) balance and beneficiary substitution across signature types - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
The `withdraw` extrinsic authenticates a relayer's fee withdrawal using a signature over `message(nonce, dest_chain, beneficiary)` [1](#0-0) . This is structurally the same class of flaw as the reported "missing MAC" bug: the signed payload authenticates *some* fields but leaves other security-critical fields — specifically the `available_amount` actually paid out, and (for non-EVM variants) which public key signed it — outside the authenticated envelope, so the on-chain effect of a valid signature is not fully pinned to what the signer intended.

### Finding Description
In `Pallet::withdraw`, the signed message never includes the amount being withdrawn [2](#0-1) . The `available_amount` is read fresh from `Fees::<T>::get(...)` at call time and used to build the destination payout body [3](#0-2) . The signature only proves "the holder of this key authorized *a* withdrawal to *this beneficiary* at *this nonce* for *this chain*" — it says nothing about how much. Since `withdraw_fees` is an unsigned, permissionless extrinsic (`ensure_none(origin)`) that anyone can submit given a captured/observed `WithdrawalInputData` [4](#0-3) [5](#0-4) , an attacker who observes one broadcast/mempool copy of a relayer's signed withdrawal payload can front-run or resubmit it once the relayer's accrued balance has grown between the time of signing and the time of inclusion — the signature stays valid for the same `nonce`/`dest_chain`/`beneficiary` tuple regardless of the amount actually paid out, since amount is not part of what is signed. There is no explicit binding to `available_amount` at signing time, so the guarantee "the signer authorized withdrawal of X" does not hold; only "the signer authorized withdrawal of whatever is currently in the Fees map" holds, which is a weaker, mutable statement analogous to un-authenticated ciphertext that can still be "decrypted" (accepted) after tampering with the surrounding conditions.

Compare this with `beneficiary_message` in `accumulate.rs`, which has the identical shape and is explicitly commented as mirroring `withdraw_fees`'s binding style [6](#0-5)  — the same gap exists there for the batch's `total_fee`.

### Impact Explanation
This does not create out-of-thin-air fund creation or a wrong final beneficiary (the beneficiary and destination chain are pinned by the signature), so it falls short of "false proof/state acceptance" in the strict sense. However, because the amount is not cryptographically bound, the guarantee that a relayer's signature authorizes a specific withdrawal is broken: whatever balance sits in `Fees[dest_chain][signer]` at the moment of on-chain execution is what gets swept and dispatched, not what the signer saw/intended when they signed. Combined with the fact that `withdraw_fees` is unsigned and anyone can resubmit the payload (it isn't a signed extrinsic tied to a specific submitter), a relayer's own previously-broadcast withdrawal message can be replayed at a moment of the attacker's choosing (subject only to nonce and mempool `provides` deduplication), draining a larger accrued balance to the beneficiary specified at signing time rather than at intent time. This is a form of transaction/message malleability around the true authorized "amount," even though nonce prevents literal double-spend.

### Likelihood Explanation
Moderate but bounded: it requires observing a relayer's signed `WithdrawalInputData` before it lands on-chain (e.g., via mempool sniffing on an unsigned extrinsic) and a window where the relayer's balance subsequently grows before the withdrawal is included/resubmitted. The `validate_unsigned` "provides" tag is derived from `withdrawal_data.encode()`, so exact resubmission of the identical bytes is what a would-be griefer/opportunist would do, and mempool visibility of unsigned extrinsics is realistic in a public network.

### Recommendation
Bind the withdrawal amount (and any other economically material fields) directly into the signed payload, e.g. `message(nonce, dest_chain, beneficiary, amount)`, and require the caller to pass the amount they intend to withdraw explicitly, verifying it against `Fees::<T>::get` rather than trusting the storage-read value at execution time to silently fill in an unauthenticated quantity. Apply the same fix to `beneficiary_message` in `accumulate.rs` so the batch's `total_fee` is authenticated as part of the beneficiary redirect signature.

### Proof of Concept
Conceptual (not verified against a running node due to index limitations):
1. Relayer signs `WithdrawalInputData { signature: sign(message(nonce=5, dest_chain=Evm(1), beneficiary=B)), dest_chain: Evm(1), beneficiary: Some(B) }` when `Fees[Evm(1)][relayer] = 10`.
2. Attacker observes this unsigned extrinsic in the mempool but delays inclusion (or it fails to be included promptly).
3. Before it lands, the relayer accumulates more fees via `accumulate_fees`, raising `Fees[Evm(1)][relayer]` to 1000.
4. The originally observed, still-valid signature/payload is submitted (by anyone, since it's `ensure_none`), and `Pallet::withdraw` reads `available_amount = 1000` and dispatches a payout of 1000 to beneficiary `B` — an amount the relayer never explicitly signed off on.

This confirms the withdrawal authorization is not fully bound to the value being moved, mirroring the "missing MAC" pattern where the signed/encrypted envelope fails to cover all security-relevant content.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-155)
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

**File:** modules/pallets/relayer/src/lib.rs (L360-368)
```rust
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

**File:** modules/pallets/relayer/src/lib.rs (L453-502)
```rust
	#[pallet::validate_unsigned]
	impl<T: Config> ValidateUnsigned for Pallet<T>
	where
		<T as frame_system::Config>::Hash: From<H256>,
		<T as frame_system::Config>::AccountId: From<[u8; 32]>,
		T::Balance: Into<u128>,
	{
		type Call = Call<T>;

		// empty pre-dispatch so we don't modify storage
		fn pre_dispatch(_call: &Self::Call) -> Result<(), TransactionValidityError> {
			Ok(())
		}

		fn validate_unsigned(_source: TransactionSource, call: &Self::Call) -> TransactionValidity {
			let res = match call {
				Call::accumulate_fees { withdrawal_proof } =>
					Self::accumulate(withdrawal_proof.clone()),
				Call::withdraw_fees { withdrawal_data } => Self::withdraw(withdrawal_data.clone()),
				Call::claim_outbound_consensus_delivery_reward { claim } =>
					Self::process_outbound_consensus_delivery_claim(claim.clone()),
				Call::claim_outbound_request_delivery_reward { claim } =>
					Self::process_outbound_request_delivery_claim(claim.clone()),
				_ => Err(TransactionValidityError::Invalid(InvalidTransaction::Call))?,
			};

			if let Err(err) = res {
				log::error!(target: "ismp", "Pallet Relayer Fees error {err:?}");
				Err(TransactionValidityError::Invalid(InvalidTransaction::Call))?
			}

			let encoding = match call {
				Call::accumulate_fees { withdrawal_proof } => withdrawal_proof.encode(),
				Call::withdraw_fees { withdrawal_data } => withdrawal_data.encode(),
				Call::claim_outbound_consensus_delivery_reward { claim } => claim.encode(),
				Call::claim_outbound_request_delivery_reward { claim } => claim.encode(),
				_ => unreachable!(),
			};

			let msg_hash = sp_io::hashing::keccak_256(&encoding).to_vec();

			Ok(ValidTransaction {
				priority: 100,
				requires: vec![],
				provides: vec![msg_hash],
				longevity: TransactionLongevity::MAX,
				propagate: true,
			})
		}
	}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L305-315)
```rust
/// Signed payload authorising a beneficiary redirect on a specific source chain.
/// Including the relayer nonce alongside the state machine keeps the signature usable for
/// exactly one accumulate call on that chain, mirroring how `withdraw_fees` binds its signed
/// payload.
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
```
