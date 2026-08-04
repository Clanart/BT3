This confirms the vulnerability path. Critically, there is no `with_transaction`/`storage_layer`/`#[transactional]` usage anywhere in the repo [1](#0-0) , meaning `on_accept` storage writes are **not** automatically rolled back by Substrate's runtime when the callback later returns `Err`. Combined with `pallet_hyper_fungible_token`'s `on_accept`, which mints/transfers tokens to the beneficiary *before* attempting an optional, fallible cross-chain calldata dispatch, this reproduces the exact bug class from the report.

### Title
Double-minting via `on_accept` replay when optional calldata execution fails after funds are already credited - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`pallet_hyper_fungible_token::on_accept` mints/transfers the bridged amount to the beneficiary **before** executing the message's optional calldata. If that optional-calldata step fails, the whole `on_accept` returns `Err`, which causes `pallet-ismp`'s request handler to delete the just-stored request receipt "so it can be retried" [2](#0-1) . Because there is no transactional wrapper around the callback invocation, the earlier mint/transfer is **not rolled back**, yet the message is now replayable. A relayer resubmitting the same `RequestMessage`/proof triggers `on_accept` again, minting the same amount a second time.

### Finding Description
`on_accept` performs, in order: (1) resolve `local_asset_id`, (2) decode message, (3) convert amount, (4) **mint or transfer funds to the beneficiary** [3](#0-2) , and only *after* that, (5) optionally decode and dispatch an arbitrary `RuntimeCall` extracted from `message.data`, gated by `BaseCallFilter` and signature checks [4](#0-3) . Any failure in step (5) — a bad signature, a call blocked by `BaseCallFilter` (e.g. maintenance/SafeMode), or the dispatched call itself returning an error via `.map_err(HftError::CallDispatchError(e.error))?` — causes the whole `on_accept` to return `Err`.

At the pallet-ismp layer, the handler stores the request receipt *before* calling `on_accept`, then explicitly deletes it if the module returns `Err`, "so it can be timed out" (replayed): [5](#0-4) . Documentation confirms this is a known, generic hazard pushed onto module implementers: "if `IsmpModule::on_accept` does not return `Ok`, the receipt... will not be persisted, allowing the request to be replayed. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying its internal state to prevent partial state changes." [6](#0-5) 

`pallet_hyper_fungible_token` violates exactly this invariant: it performs the irreversible currency mint/transfer *before* the fallible calldata-execution step, instead of after all fallible operations complete (which is the pattern correctly followed in its own `on_timeout` function, where the transfer is the last operation) [7](#0-6) . No search across the repo found any `with_transaction`/`#[transactional]` wrapper around the `on_accept` invocation that would auto-revert the mint on later failure.

This directly mirrors the external report's pattern: a transaction body executes partially, hits a revert/`Err` condition, yet "the permission to execute remains with this transaction" (the receipt is deleted, re-enabling execution), so the failed transaction can be completed later — here, completed *again*, minting funds twice for one bridged transfer.

### Impact Explanation
This is unauthorized double-minting / loss of funds at the protocol level: a single cross-chain deposit can be credited to the beneficiary's balance multiple times by repeatedly triggering the fallible-calldata branch to fail (or by simply resubmitting the proof while the call filter is transiently blocking the embedded call), inflating the local asset supply against the escrowed/burned amount on the source chain. This falls squarely within "stealing or loss of funds" and "replay/double-claim/double-settlement" impact categories.

### Likelihood Explanation
The optional-calldata path is attacker-controlled: the `message.data` field including the `SubstrateCalldata` (target `RuntimeCall` and optional signature) originates from the bridged message payload. An attacker who controls or colludes with the message-creation side (e.g., their own EVM-side sender contract dispatching to this pallet) can craft a message whose calldata deterministically fails dispatch (e.g., a call rejected by `BaseCallFilter`, or an invalid signature) while still carrying a valid amount/beneficiary. Any relayer resubmitting the valid membership proof (permissionless, per the `handle_incoming_message` request path) re-triggers `on_accept`, and the mint happens again. No relayer/prover/admin misbehavior is required — this is a public entrypoint (message delivery) reachable by any unprivileged actor holding the source-chain proof, which is trivially obtainable once the original message is finalized.

### Recommendation
Reorder `on_accept` so all fallible validation and execution (including calldata decode, signature verification, `BaseCallFilter` check, and `RuntimeCall::dispatch`) happens **before** any irreversible mint/transfer, mirroring the safe ordering already used in `on_timeout`. Alternatively, wrap the entire `on_accept` body execution in `pallet-ismp`'s request handler in a `frame_support::storage::with_transaction` (transactional storage layer) so that any `Err` returned by a module callback atomically rolls back all storage mutations performed during that callback, guaranteeing "receipt deleted" and "state reverted" always occur together.

### Proof of Concept
1. Source chain dispatches a `Send` message with valid `amount`/`beneficiary`, plus non-empty `message.data` encoding a `SubstrateCalldata` whose embedded `RuntimeCall` is filtered by `BaseCallFilter` on the destination runtime (or whose signature check will fail).
2. Relayer submits `RequestMessage` with a valid membership proof; `pallet-ismp` stores the request receipt, then calls `on_accept`.
3. `on_accept` mints/transfers `amount` to `beneficiary` (state committed), then reaches the calldata-dispatch step, which fails `BaseCallFilter::contains` (or signature verification), returning `Err(HftError::CallFiltered)` (or `SignatureVerificationFailed`).
4. `pallet-ismp`'s request handler sees `res.is_err()` and calls `host.delete_request_receipt(&wrapped_req)` [8](#0-7) , clearing replay protection while the mint from step 3 remains applied.
5. Any relayer resubmits the identical `RequestMessage`/proof (still valid, since no receipt exists and the request has not timed out); `on_accept` runs again, minting `amount` a second time to the same `beneficiary` — net result: two mints backed by one source-chain escrow/burn.

### Citations

**File:** modules/ismp/core/src/handlers/request.rs (L99-126)
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
```

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

**File:** docs/content/protocol/ismp/requests.mdx (L111-113)
```text
<Callout title={'Danger'} type={"warn"}>
It's important to note that if the `IsmpModule::on_accept` does not return `Ok`, the receipt of this request will not be persisted, allowing the request to be **replayed**. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying it's internal state to prevent partial state changes that could result in critical vulnerabilities in their request handler. This model ensures that if a request cannot be executed successfully on a destination state machine, it can time out gracefully on the source.
</Callout>
```
