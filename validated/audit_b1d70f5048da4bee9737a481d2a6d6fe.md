## Finding: Calldata Replay via Beneficiary Account Reaping/Recreation Resets `frame_system` Nonce (`hyper-fungible-token`)

### Title
Signed `SubstrateCalldata` runtime-call authorization is replayable after beneficiary account reaping resets `frame_system` nonce - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`Pallet::on_accept` authenticates an optional cross-chain `runtime_call` by verifying a signature over `(frame_system::account_nonce(beneficiary), runtime_call)`, then calls `inc_account_nonce(origin)` once the call dispatches successfully. [1](#0-0) [2](#0-1)  This ties calldata-replay protection to the *general-purpose* `frame_system::Account` nonce rather than to a dedicated, monotonic, pallet-owned replay guard. If the beneficiary account is fully drained (e.g., via the pallet's own `ExistenceRequirement::AllowDeath` transfer used both in `on_accept` and `on_timeout`) and reaped by `frame_system`, its `Account` storage entry — including the nonce — is deleted. Once the account is later re-funded (recreated) by any subsequent cross-chain deposit, its nonce restarts at `0`. Any previously-used, publicly-visible `(nonce, runtime_call)` signature that matched a low/already-consumed nonce becomes valid again, letting an attacker re-submit a fresh, legitimately-proven ISMP `PostRequest` (a distinct commitment, so it is not blocked by ISMP's own per-request duplicate-delivery receipt) carrying the old `SubstrateCalldata` bytes, causing the previously authorized `runtime_call` to dispatch a second time with the beneficiary as origin.

### Finding Description
The `SubstrateCalldata` struct documents its own security model: "the account nonce is incremented after dispatch to prevent replay." [3](#0-2)  The implementation reads the beneficiary's nonce, verifies the signature against `(nonce, runtime_call)` for Ed25519/Sr25519/ECDSA, dispatches with `RawOrigin::Signed(origin)`, then calls `frame_system::Pallet::<T>::inc_account_nonce(origin)`. [4](#0-3) 

This scheme assumes the `frame_system` nonce for an account is permanently monotonic. It is not: standard Substrate account-reaping semantics remove the entire `Account<T>` entry (including its nonce) once an account's balance drops to zero and it has no remaining providers, and native/asset transfers issued by this same pallet use `AllowDeath`/`Preservation::Expendable`, which permit exactly this kind of reaping. [5](#0-4) [6](#0-5)  When the account is later recreated by any incoming deposit, its nonce restarts at `0`, so a signature computed once over `(0, runtime_call)` becomes valid again.

Because the guard against reuse of a signed authorization is *only* "does the signed nonce equal the beneficiary's current `frame_system` nonce," and not any dedicated, non-resettable, per-signature/per-message replay set, the protection silently disappears whenever the nonce cycles back to a previously-used value. ISMP's own receipt/commitment-based duplicate-delivery protection does not help here because the attacker submits a *new*, legitimately-provable `PostRequest` (a different commitment/outer nonce) that merely repeats the *inner* `SubstrateCalldata` bytes from an earlier message — this is exactly the "duplicate guard" the review path calls out as needing to be checked, and it does not cover this inner payload.

### Impact Explanation
An attacker who possesses a previously used (and hence publicly observable, since the message and its calldata travel on-chain/cross-chain) `SubstrateCalldata` signature can cause the authorized `runtime_call` to be re-dispatched a second time as soon as the beneficiary account's nonce cycles back to the signed value via reaping + recreation. Since the calldata is executed with `RawOrigin::Signed(beneficiary)` immediately after this same `on_accept` call mints/transfers a fresh cross-chain deposit into that same beneficiary, an attacker-crafted replay can be used to redirect newly arrived bridged funds (e.g., re-triggering an old `Balances::transfer_allow_death` to an attacker-chosen `final_recipient`) without any fresh authorization from the beneficiary. This matches the bounty's "unauthorized transaction or execution" / "replay/double-claim" categories and can cause wrongful movement of bridged funds.

### Likelihood Explanation
Exploitation requires the beneficiary account to be fully drained to zero (removable via existing `AllowDeath`/`Expendable` transfer paths already present in this pallet) and later re-funded — a state that is plausible for cross-chain "smart contract"-like beneficiary accounts that hold only bridged balances and receive intermittent deposits. The attacker does not need a malicious relayer, forged proof, or privileged role; they only need a legitimately provable new cross-chain message carrying old calldata bytes, and the timing coincidence of the nonce cycling back to the previously signed value (trivially achievable if the original signature used nonce `0`, the very first nonce any fresh/recreated account will have).

### Recommendation
Decouple calldata replay protection from the generic, resettable `frame_system` account nonce. Use a dedicated, pallet-owned, monotonic anti-replay mechanism instead — e.g., include the unique ISMP request identifier/commitment (or a pallet-managed counter stored independently of account existence) in the signed payload, or maintain a separate `StorageMap` of consumed calldata hashes/nonces per beneficiary that is never cleared by account reaping.

### Proof of Concept
1. Bridge tokens to beneficiary `B` with `SubstrateCalldata{ signature: sign(0, call_C), runtime_call: call_C }`; `on_accept` dispatches `call_C` as `B` and increments `B`'s nonce to `1`.
2. Drain `B`'s entire balance to zero using an `AllowDeath` transfer (e.g., have `call_C` itself, or a follow-up transfer, empty `B`); `frame_system` reaps `B`, removing its `Account` entry (nonce reset).
3. Trigger any new legitimate cross-chain deposit to `B` (a distinct ISMP `PostRequest`/commitment) re-funding and recreating `B`, whose nonce is now `0` again.
4. In that same or a subsequent message, attach the original `SubstrateCalldata{ signature: sign(0, call_C), runtime_call: call_C }` bytes; `on_accept` reads `nonce = 0`, signature verifies successfully again, and `call_C` is dispatched a second time as `B` — extend the existing test `should_receive_asset_with_calldata` (which already demonstrates same-message replay is blocked) [7](#0-6)  by reaping and re-funding `B` between two *different* `on_accept` calls carrying identical `substrate_data`, and assert the second dispatch succeeds instead of erroring.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L94-117)
```rust
		if local_asset_id == T::NativeAssetId::get() {
			<T as Config>::NativeCurrency::transfer(
				&Pallet::<T>::pallet_account(),
				&beneficiary,
				amount,
				ExistenceRequirement::AllowDeath,
			)
			.map_err(|e| HftError::TransferFailed(e.into()))?;
		} else {
			let is_native = NativeAssets::<T>::get(local_asset_id.clone());
			if is_native {
				<T as Config>::Assets::transfer(
					local_asset_id,
					&Pallet::<T>::pallet_account(),
					&beneficiary,
					amount.into(),
					Preservation::Expendable,
				)
				.map_err(|e| HftError::TransferFailed(e.into()))?;
			} else {
				<T as Config>::Assets::mint_into(local_asset_id, &beneficiary, amount.into())
					.map_err(|e| HftError::MintFailed(e.into()))?;
			}
		}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L124-202)
```rust
			let origin = if let Some(signature) = substrate_data.signature {
				let multi_signature = MultiSignature::decode(&mut &*signature)
					.map_err(HftError::SignatureDecodeError)?;

				let nonce = frame_system::Pallet::<T>::account_nonce(beneficiary.clone());

				match multi_signature {
					MultiSignature::Ed25519(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
						let msg = sp_io::hashing::keccak_256(&payload);
						let pub_key = beneficiary_bytes
							.as_slice()
							.try_into()
							.map_err(|_| HftError::SignatureVerificationFailed)?;
						if !sp_io::crypto::ed25519_verify(&sig, msg.as_ref(), &pub_key) {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Sr25519(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
						let msg = sp_io::hashing::keccak_256(&payload);
						let pub_key = beneficiary_bytes
							.as_slice()
							.try_into()
							.map_err(|_| HftError::SignatureVerificationFailed)?;
						if !sp_io::crypto::sr25519_verify(&sig, msg.as_ref(), &pub_key) {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Ecdsa(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
						let preimage = vec![
							format!("{ETHEREUM_MESSAGE_PREFIX}{}", payload.len())
								.as_bytes()
								.to_vec(),
							payload,
						]
						.concat();
						let msg = sp_io::hashing::keccak_256(&preimage);
						let pub_key = sp_io::crypto::secp256k1_ecdsa_recover(&sig.0, &msg)
							.map_err(|_| HftError::EcdsaRecoveryFailed)?;
						let eth_address =
							H160::from_slice(&sp_io::hashing::keccak_256(&pub_key[..])[12..]);
						let substrate_account = T::EvmToSubstrate::convert(eth_address);
						if substrate_account != beneficiary {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Eth(_) => Err(HftError::EthSignatureUnsupported)?,
				};

				beneficiary.clone()
			} else {
				let from_bytes = message.from.as_ref();
				if source.is_evm() {
					T::EvmToSubstrate::convert(H160::from_slice(
						&from_bytes[from_bytes.len() - 20..],
					))
				} else {
					let mut account = [0u8; 32];
					account.copy_from_slice(from_bytes);
					account.into()
				}
			};

			let runtime_call = T::RuntimeCall::decode(&mut &*substrate_data.runtime_call)
				.map_err(HftError::RuntimeCallDecodeError)?;
			// Apply the runtime's base call filter so that cross-chain calls cannot
			// reach dispatchables that the runtime has otherwise filtered out (e.g.
			// during a maintenance mode or a SafeMode period).
			if !<T as frame_system::Config>::BaseCallFilter::contains(&runtime_call) {
				Err(HftError::CallFiltered)?
			}
			use sp_runtime::traits::Dispatchable;
			runtime_call
				.dispatch(RawOrigin::Signed(origin.clone()).into())
				.map_err(|e| HftError::CallDispatchError(e.error))?;

			frame_system::Pallet::<T>::inc_account_nonce(origin);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L257-285)
```rust
				// Refund: release escrowed tokens back to the original sender
				if local_asset_id == T::NativeAssetId::get() {
					<T as Config>::NativeCurrency::transfer(
						&Pallet::<T>::pallet_account(),
						&beneficiary,
						amount.into(),
						ExistenceRequirement::AllowDeath,
					)
					.map_err(|e| HftError::TransferFailed(e.into()))?;
				} else {
					let is_native = NativeAssets::<T>::get(local_asset_id.clone());
					if is_native {
						<T as Config>::Assets::transfer(
							local_asset_id,
							&Pallet::<T>::pallet_account(),
							&beneficiary,
							amount.into(),
							Preservation::Expendable,
						)
						.map_err(|e| HftError::TransferFailed(e.into()))?;
					} else {
						<T as Config>::Assets::mint_into(
							local_asset_id,
							&beneficiary,
							amount.into(),
						)
						.map_err(|e| HftError::MintFailed(e.into()))?;
					}
				}
```

**File:** modules/pallets/hyper-fungible-token/src/types.rs (L105-113)
```rust
/// SCALE-encoded calldata for executing a runtime call on the destination substrate chain
#[derive(Debug, Clone, Encode, Decode, scale_info::TypeInfo, PartialEq, Eq)]
pub struct SubstrateCalldata {
	/// Optional SCALE-encoded [MultiSignature](sp_runtime::MultiSignature) of the beneficiary's
	/// account nonce and the encoded runtime call
	pub signature: Option<Vec<u8>>,
	/// SCALE-encoded runtime call to execute
	pub runtime_call: Vec<u8>,
}
```

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L312-321)
```rust
		module.on_accept(post.clone()).unwrap();

		// The calldata transferred tokens from beneficiary to final_recipient
		let final_balance = pallet_balances::Pallet::<Test>::free_balance(final_recipient);
		assert_eq!(final_balance, SEND_AMOUNT);

		// Replay should fail (nonce incremented)
		let result = module.on_accept(post);
		assert!(result.is_err());
	});
```
