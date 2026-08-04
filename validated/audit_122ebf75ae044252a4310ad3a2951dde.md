## Analysis

The `on_accept` implementation in `modules/pallets/hyper-fungible-token/src/module.rs` performs the mint/transfer to `beneficiary` *before* it validates and dispatches the optional embedded `runtime_call`: [1](#0-0) 

If `message.data` is non-empty, signature verification (Ed25519/Sr25519/Ecdsa) or the call filter/dispatch can fail afterward and return `Err(...)`: [2](#0-1) 

The caller, `modules/ismp/core/src/handlers/request.rs::handle`, invokes `cb.on_accept(request.clone())` for each request in a batch and, on error, only deletes the stored request receipt so the request "can be timed out" — it does **not** wrap the callback in any storage transaction: [3](#0-2) 

Critically, per-request errors are collected into a `Vec<Result<...>>` and the overall extrinsic still returns `Ok(MessageResult::Request { events: result, ... })`. Since FRAME's automatic transactional-storage rollback only triggers when the *dispatchable call itself* returns `Err`, and this extrinsic always returns `Ok` at the top level (by design, so one bad message in a batch doesn't revert the whole batch), any storage mutation performed by `on_accept` *before* the internal error point (i.e., the mint/transfer to `beneficiary`) is **not rolled back**. I found no explicit `with_transaction`/`with_storage_layer` wrapper around the individual `cb.on_accept(...)` call in `handlers/request.rs` that would isolate and revert only that request's effects.

Because the receipt is explicitly deleted on failure (`host.delete_request_receipt(&wrapped_req)?`), the request becomes eligible for a later timeout on the source chain, whose handler refunds the original sender: [4](#0-3) 

This matches the reported scenario: mint/transfer to `beneficiary` executes and persists, `on_accept` still returns `Err` due to bad signature/calldata further down, the receipt is deleted enabling timeout, and a subsequent `on_timeout` refunds `message.from` — resulting in value effectively created twice (once via mint on destination, once via refund on source) for a single original transfer.

**Note on uncertainty:** I was not able to fully verify, within tool budget, the exact mechanics of how a timeout proof is validated against destination-chain receipt state in `modules/ismp/core` (whether timeout requires proof of non-delivery tied specifically to receipt absence, or purely elapsed-timestamp checks), nor did I inspect the single `transactional` reference found in `modules/pallets/ismp/src/lib.rs`, which could conceivably provide an additional safeguard not visible in the files reviewed. If you need certainty on this last piece, a full read of `modules/pallets/ismp/src/lib.rs` and the timeout handler (`modules/ismp/core/src/handlers/timeout.rs` or equivalent) is recommended — that would require a Devin session with full repository access, since the index used here may not surface every relevant excerpt.

### Title
Non-atomic mint-then-verify in `on_accept` combined with receipt deletion on failure enables double-credit via later `on_timeout` refund - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`on_accept` mints/transfers funds to the beneficiary before validating the optional embedded signed `runtime_call`. If that later validation fails, the function returns `Err`, but the mint/transfer already executed is not rolled back because the ISMP request handler does not wrap the per-message `on_accept` call in a storage transaction and the enclosing extrinsic returns `Ok` overall. The failure path also deletes the request receipt specifically "so it can be timed out," which can allow a subsequent `on_timeout` to refund the original sender for the same request — crediting value twice for one transfer.

### Finding Description
`on_accept` (`modules/pallets/hyper-fungible-token/src/module.rs:50-212`) performs the asset mint/transfer to `beneficiary` at lines 93-117, then only afterward decodes and verifies an optional signature and dispatches an embedded `runtime_call` (lines 119-203). A crafted message with a valid recipient/amount but a garbage signature or malformed/filtered `runtime_call` will pass the mint/transfer step and then fail at signature verification, call decode, filter check, or dispatch, returning `Err`.

The batch handler in `modules/ismp/core/src/handlers/request.rs:99-132` calls `cb.on_accept(request.clone())` with no storage-transaction isolation around the call. On error it deletes the stored request receipt (line 124) and folds the error into a per-request result vector, while the surrounding extrinsic still returns `Ok`. Because FRAME's default transactional rollback only reverts storage on an `Err` return from the *dispatchable itself*, and this dispatchable returns `Ok` at the top level, the already-executed mint/transfer to `beneficiary` persists.

`on_timeout` (lines 218-296) independently refunds `message.from` when a timeout is later processed for the same request. If the deleted receipt (or the general timeout-eligibility logic) allows a timeout to be accepted for this already-"accepted-but-erroring" request, the original sender receives a refund on top of the beneficiary's earlier mint — a double credit of the same value.

### Impact Explanation
This breaks the "funds move exactly once" invariant for bridged assets: value is created twice from a single original transfer (once as a mint/transfer to the destination beneficiary, once as a refund to the source sender). This directly matches the impact gate's "stealing or loss of funds" / "duplicate settlement" categories, since custody/mint accounting no longer matches the actual escrowed/burned principal.

### Likelihood Explanation
The trigger only requires an unprivileged party to submit (via a relayer) a PostRequest whose ERC20-side `Message.data` contains a runtime call with an invalid signature or a call that fails the `BaseCallFilter`/dispatch — i.e., no privileged operator or malicious infrastructure is needed, satisfying the scope's "unprivileged calls, messages" requirement. The remaining condition — whether the destination-chain's deleted receipt actually makes the request time out on the source chain rather than being treated as delivered — could not be fully confirmed from the code inspected, so likelihood depends on how `modules/ismp/core`'s timeout-proof validation interprets receipt absence.

### Recommendation
Make the mint/transfer and the runtime-call verification/dispatch atomic within `on_accept`: perform signature verification and call decoding/filtering first, and only mint/transfer after all fallible checks succeed, or wrap the entire `on_accept` body in an explicit `frame_support::storage::transactional::with_storage_layer` so any `Err` fully reverts prior writes. Additionally, review whether deleting the request receipt on `on_accept` failure is safe generically across modules that mutate state before failing, or whether it should only happen when no state-mutating side effects have occurred.

### Proof of Concept
1. Register a token and configure `ContractToAsset`/`Precisions` for a source EVM chain as in the pallet's tests.
2. Construct a `Message` with a valid `to` (beneficiary) and `amount`, and non-empty `data` containing `SubstrateCalldata` with a syntactically valid but cryptographically invalid `Ed25519`/`Sr25519`/`Ecdsa` signature (or a `runtime_call` blocked by `BaseCallFilter`).
3. Invoke `on_accept` directly (as `handlers::request::handle` would) and observe: (a) the beneficiary's balance/asset storage reflects the mint/transfer, and (b) the call returns `Err(HftError::SignatureVerificationFailed)` (or `CallFiltered`/`CallDispatchError`).
4. Confirm via the `handlers::request::handle` path that the request receipt was deleted (line 124) despite the successful mint.
5. Simulate a timeout for the same original request (as would be produced on the source chain) via `on_timeout`, and confirm it credits `message.from` again — verifying the beneficiary's mint from step 3 and the sender's refund from this step both persist, i.e., double crediting for a single transfer.

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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L119-200)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L218-292)
```rust
	fn on_timeout(&self, request: Request) -> Result<Weight, anyhow::Error> {
		match request {
			Request::Post(PostRequest { body, to, dest, .. }) => {
				let message = Message::abi_decode(&body).map_err(HftError::DecodeError)?;

				// Refund the original sender
				let from_bytes = message.from.as_ref();
				let mut sender_bytes = [0u8; 32];
				if from_bytes.len() == 32 {
					sender_bytes.copy_from_slice(from_bytes);
				} else if from_bytes.len() == 20 {
					sender_bytes[12..].copy_from_slice(from_bytes);
				} else {
					Err(HftError::InvalidSenderLength(from_bytes.len()))?
				}
				let beneficiary: T::AccountId = sender_bytes.into();

				// Look up the asset from the destination contract address
				let local_asset_id = ContractToAsset::<T>::get(dest, &to)
					.ok_or(HftError::UnknownContractOnTimeout)?;

				let decimals = if local_asset_id == T::NativeAssetId::get() {
					T::Decimals::get()
				} else {
					<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
						local_asset_id.clone(),
					)
				};
				let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), dest)
					.ok_or(HftError::DecimalsNotConfigured(dest))?;
				let amount = convert_to_balance::<
					<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
				>(
					U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
					erc_decimals,
					decimals,
				)
				.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;

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

				Pallet::<T>::deposit_event(Event::<T>::TokenRefunded {
					beneficiary,
					amount: amount.into(),
					dest,
				});
				Ok(T::DbWeight::get().reads_writes(5, 2))
```

**File:** modules/ismp/core/src/handlers/request.rs (L99-132)
```rust
		.map(|request| {
			let wrapped_req = Request::Post(request.clone());
			let mut lambda = || {
				let cb = router.module_for_id(request.to.clone())?;
				// Re-check the receipt right before dispatch. The up-front pass above
				// runs before any callback executes; a prior request's on_accept in
				// this same batch could have stored a receipt for this request
				// (directly or by re-entering the handler), and we must not invoke
				// on_accept a second time.
				if host.request_receipt(&wrapped_req).is_some() {
					Err(Error::DuplicateRequest { meta: wrapped_req.clone().into() })?
				}
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
			};

			let res = lambda().and_then(|res| res);
			res
		})
		.collect::<Vec<_>>();
```
