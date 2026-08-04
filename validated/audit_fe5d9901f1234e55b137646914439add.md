## Title
Duplicate Fund Crediting via Receipt Deletion on Post-Mint Calldata Decode Failure - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`HyperFungibleToken::on_accept` performs the irreversible beneficiary mint/transfer (lines 93–117) *before* decoding and dispatching the optional `call_data` payload (lines 119–203). If the embedded `runtime_call` bytes fail `T::RuntimeCall::decode` (line 189–190), `on_accept` returns `Err(HftError::RuntimeCallDecodeError)` after the funds have already been credited. `pallet-ismp`'s request handler deletes the request receipt whenever the module callback returns `Err`, which allows the identical request to be resubmitted and reprocessed until it times out — reproducing the mint/transfer each time.

### Finding Description
In `modules/pallets/hyper-fungible-token/src/module.rs`, `on_accept` mints or transfers funds to the beneficiary first: [1](#0-0) 

Then, only if `message.data` is non-empty, it decodes and dispatches the embedded runtime call, which can fail deterministically: [2](#0-1) 

`HftError::RuntimeCallDecodeError` is defined and surfaced from this exact failure point: [3](#0-2) 

The ISMP core request handler (`modules/ismp/core/src/handlers/request.rs`) stores a receipt before invoking `on_accept`, but explicitly deletes that receipt if the callback returns `Err`: [4](#0-3) 

This behavior — and the resulting hazard — is explicitly documented as a known anti-pattern that module authors must avoid: [5](#0-4) 

Because the up-front duplicate check in `handle()` only rejects requests that still have a stored receipt, and the receipt was deleted after the failed `on_accept`, the *same* `PostRequest` (with the same proof, since the underlying source-chain commitment is unchanged) can be resubmitted via `handle()` repeatedly, as long as it hasn't timed out: [6](#0-5) 

Each resubmission re-enters `on_accept`, re-executes the mint/transfer at lines 93–117, and deterministically fails again at the same `runtime_call` decode step, re-deleting the receipt and re-enabling the next replay — up to the request's `timeout_timestamp`.

### Impact Explanation
An unprivileged sender who controls the outbound message on the source chain (e.g., calling the paired EVM `HyperFungibleToken` contract's send function with a legitimate signature/amount but an intentionally undecodable `SubstrateCalldata.runtime_call`) can cause the destination pallet to credit the beneficiary multiple times for a single source-chain commitment before the message times out. This is a direct violation of the "one commitment settles exactly once" invariant and results in duplicated bridged fund credit (theft of custodied/mint authority funds), which falls squarely within the "stealing or loss of funds" / "duplicate settlement" impact categories.

### Likelihood Explanation
The trigger conditions are fully attacker-controlled and require no privileged role, malicious relayer, or compromised infrastructure: a registered asset, a valid top-level message signature (verified by the EVM contract during send, not by this decode step), and a `call_data` payload whose `runtime_call` bytes are simply garbage/undecodable SCALE bytes. Any relayer — even an honest one — resubmitting the request after the first failed attempt (since the receipt no longer exists) reproduces the double-credit. This can be repeated multiple times within the request's timeout window.

### Recommendation
Reorder `on_accept` so all fallible operations related to `call_data` (decode, signature verification, `BaseCallFilter` check) occur *before* the beneficiary mint/transfer, or stage the calldata dispatch such that failure to dispatch does not roll back the already-committed transfer while still returning `Err` (e.g., swallow non-critical calldata dispatch failures and emit an event instead of propagating an `Err` from `on_accept`, so the receipt is retained and the request cannot be replayed). At minimum, ensure that once the balance-affecting mint/transfer has occurred, `on_accept` never returns `Err`.

### Proof of Concept
1. Register an asset and configure `ContractToAsset`/`Precisions` normally.
2. Construct a `PostRequest` whose ABI-decoded `Message.data` contains a `SubstrateCalldata` with `signature: None` (or a validly-signed) and `runtime_call: vec![0xFF; N]` (bytes that cannot decode into any `T::RuntimeCall` variant).
3. Call `module.on_accept(post.clone())` — observe: beneficiary balance increases (mint/transfer at lines 93–117 succeeds), then the call returns `Err(HftError::RuntimeCallDecodeError)`.
4. In the full `pallet-ismp` flow, `handlers::request::handle` would see `res.is_err()` and call `host.delete_request_receipt(&wrapped_req)` (lines 122–124 of `handlers/request.rs`), removing the request's receipt.
5. Resubmit the exact same `RequestMessage` (same proof, same request, still not timed out) through `handle()` again — the up-front `host.request_receipt(&req).is_some()` check (line 58) now passes since no receipt exists, so `on_accept` is invoked again, minting the beneficiary a second time.
6. Repeat until `req.timed_out(host.timestamp())` becomes true, extracting one duplicate credit per replay.

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

**File:** modules/pallets/hyper-fungible-token/src/error.rs (L51-52)
```rust
	#[error("RuntimeCall decode error: {0}")]
	RuntimeCallDecodeError(codec::Error),
```

**File:** modules/ismp/core/src/handlers/request.rs (L55-65)
```rust
	for req in msg.requests.iter() {
		let req = Request::Post(req.clone());
		// If a receipt exists for any request then it's a duplicate and it is not dispatched
		if host.request_receipt(&req).is_some() {
			Err(Error::DuplicateRequest { meta: req.clone().into() })?
		}

		// can't dispatch timed out requests
		if req.timed_out(host.timestamp()) {
			Err(Error::RequestTimeout { meta: req.clone().into() })?
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

**File:** docs/content/protocol/ismp/requests.mdx (L109-113)
```text
- Finally dispatch the requests to the relevant `IsmpModule::on_accept` and store a receipt for each request to prevent requests from being replayed.

<Callout title={'Danger'} type={"warn"}>
It's important to note that if the `IsmpModule::on_accept` does not return `Ok`, the receipt of this request will not be persisted, allowing the request to be **replayed**. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying it's internal state to prevent partial state changes that could result in critical vulnerabilities in their request handler. This model ensures that if a request cannot be executed successfully on a destination state machine, it can time out gracefully on the source.
</Callout>
```
