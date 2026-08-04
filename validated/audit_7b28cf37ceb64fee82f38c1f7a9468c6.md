## Analog Found

### Title
Token mint/transfer executes before fallible signed-calldata dispatch in `on_accept`, enabling unlimited replay-mint of bridged funds - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`Pallet::on_accept` in the hyper-fungible-token ISMP module mints/transfers the bridged amount to the beneficiary **before** it decodes and dispatches the optional signed calldata payload. If that later step fails for any reason, the whole `on_accept` returns `Err`. The generic ISMP request handler treats a failed callback as "not yet processed": it deletes the just-stored request receipt so the message can be retried. Because the mint/transfer already happened and is not part of any transactional rollback, every retry of the same request re-executes the mint, letting an attacker cause the bridge to mint the same deposit repeatedly until the request times out.

### Finding Description
In `on_accept` [1](#0-0) , the pallet transfers/mints `amount` to `beneficiary` unconditionally. Only afterward does it decode and execute the optional signed `SubstrateCalldata` payload [2](#0-1) , which can fail via `HftError::SignatureDecodeError`, `SignatureVerificationFailed`, `EcdsaRecoveryFailed`, `RuntimeCallDecodeError`, `CallFiltered`, or `CallDispatchError` — any of which propagate as `Err` out of `on_accept`.

The generic ISMP request handler stores a request receipt right before invoking the module callback, and explicitly **deletes** that receipt if the callback returns `Err`, specifically so the message "can be timed out" (i.e. retried): [3](#0-2) . The handler contains no `with_transaction`/transactional wrapper around the callback invocation, so any storage mutation the callback already performed (the mint/transfer) is **not rolled back** when it later returns `Err`.

This exact hazard is documented in the project's own developer guide: "The `IsmpHost` does not store receipts for failed messages... This effectively allows messages to be re-executed until they time out. **Therefore you should ensure irreversible state changes occur only after a message effectively meets all success criteria**." [4](#0-3) . `pallet-hyper-fungible-token::on_accept` violates this guidance directly — the irreversible mint/transfer happens *before* the callback's success criteria (valid signature, filtered/dispatchable call) are checked.

### Impact Explanation
An attacker who controls the source-side message (e.g., the depositor on the EVM side crafting the `Message.data` field) can attach a `SubstrateCalldata` payload that is guaranteed to fail deterministically at execution time — for example, a call that is rejected by `BaseCallFilter` (`HftError::CallFiltered`), or a syntactically valid but undecodable `runtime_call` (`HftError::RuntimeCallDecodeError`), or a bad `MultiSignature` (`HftError::SignatureVerificationFailed`). Because the failure happens *after* minting, each relay attempt of that same request:
1. Passes the "no existing receipt" duplicate check (receipt was deleted on the previous failed attempt).
2. Mints/transfers the deposit amount to `beneficiary` again.
3. Fails at the calldata step again (deterministically the same failure) and the receipt is deleted again.

This lets any relayer's re-delivery of the same still-not-timed-out request cause the bridge to double-mint (or N-times mint) tokens for a single bridged deposit, directly matching "stealing or loss of funds" / "replay / double-claim / double-settlement."

### Likelihood Explanation
No malicious relayer, prover, or admin is required — an ordinary relayer simply redelivering an in-flight (not yet timed out) request is sufficient, and message redelivery/retry before timeout is normal, expected ISMP behavior (as the docs describe). The attacker only needs to control the outgoing message body from the source chain (a normal, permissionless bridging user action), crafting calldata that deterministically fails post-mint. This is a pure logic/ordering bug in first-party code, not a front-running or peer-compromise scenario, so it survives the impact gate.

### Recommendation
Reorder `on_accept` so that all fallible steps (signature verification, calldata decoding, call-filter check, and dispatch) happen **before** the token mint/transfer, or wrap the entire `on_accept` body (mint + calldata execution) in `frame_support::storage::with_transaction` so any `Err` return rolls back the mint along with everything else. This aligns the implementation with the documented replay-safety invariant that irreversible state changes must only occur once all success criteria are met.

### Proof of Concept
1. Attacker deposits on the source chain and constructs `Message.data` containing a `SubstrateCalldata` with a valid amount/beneficiary but a `runtime_call` that is filtered by the destination runtime's `BaseCallFilter` (or an invalid `signature` field).
2. Relayer delivers the resulting `PostRequest`; `pallet_ismp::child_trie` stores a request receipt, then `on_accept` runs: it mints/transfers `amount` to `beneficiary`, then fails at calldata dispatch, returning `Err(HftError::CallFiltered)` (or similar).
3. Per `modules/ismp/core/src/handlers/request.rs`, the request receipt is deleted since the callback errored.
4. Any relayer (or the same one) redelivers the identical, still-not-timed-out request. The duplicate-receipt check passes (no receipt exists), and `on_accept` mints/transfers `amount` again, then fails identically and the receipt is deleted again.
5. Repeat until the request's `timeout_timestamp` elapses — each iteration mints the beneficiary's account by `amount`, producing unbacked/duplicated bridged tokens from a single source-chain deposit.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L93-117)
```rust
		// Mint or transfer to beneficiary
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L119-203)
```rust
		// Execute optional calldata
		if !message.data.is_empty() {
			let substrate_data = SubstrateCalldata::decode(&mut &message.data[..])
				.map_err(HftError::CalldataDecodeError)?;

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
		}
```

**File:** modules/ismp/core/src/handlers/request.rs (L111-126)
```rust
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
```

**File:** docs/content/developers/polkadot/receiving.mdx (L175-180)
```text
## Security Considerations

<Callout title={'Replay Attack Warning'} type={"warn"}>

The `IsmpHost` does not store receipts for failed messages. ie messages whose `IsmpModule` returns `Err`. This effectively allows messages to be re-executed until they time out. **Therefore you should ensure irreversible state changes occur only after a message effectively meets all success criteria**.
</Callout>
```
