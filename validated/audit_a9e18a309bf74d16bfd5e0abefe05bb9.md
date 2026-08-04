### Title
Mint executes before calldata/signature validation in `on_accept`, enabling double-mint via receipt-deletion replay - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`HyperFungibleToken::on_accept` credits (mints/transfers) the bridged amount to the beneficiary at [1](#0-0)  before it decodes and validates the optional `SubstrateCalldata`/`MultiSignature` payload at [2](#0-1) . Any failure in that later validation (e.g. `SignatureDecodeError`, `CalldataDecodeError`, `SignatureVerificationFailed`, `EcdsaRecoveryFailed`, `RuntimeCallDecodeError`, `CallFiltered`, `CallDispatchError`) causes `on_accept` to return `Err` after the mint has already been committed to storage.

### Finding Description
`pallet-ismp`'s request handler stores a request receipt, invokes `IsmpModule::on_accept`, and explicitly **deletes the receipt if the callback errors**, so the request can be retried: [3](#0-2) 

Crucially, each per-request result is captured into `Ok(res)` and only surfaces inside `MessageResult::Request { events, .. }` — the outer `handle()` function itself still returns `Ok(...)`, so the overall extrinsic succeeds and any storage transaction wrapping the whole extrinsic does **not** roll back the mint that already happened inside the failed `on_accept` call: [4](#0-3) 

This exact hazard is called out explicitly in the protocol's own documentation, placing the burden on the module implementer to avoid partial state changes: [5](#0-4) 

`hyper-fungible-token`'s `on_accept` violates this invariant: it mints/transfers funds first, then only afterward decodes calldata and a `MultiSignature`: [6](#0-5) 

Because the request receipt is deleted on failure, and the source-chain request commitment/leaf used for the membership proof is untouched (nothing deletes it on a failed accept), the identical `PostRequest` (same commitment, same still-valid membership proof against the committed state root) can be resubmitted through the same permissionless `handle` message path. The up-front duplicate check in `handle()` only rejects resubmission if a receipt still exists: [7](#0-6) 

Since the receipt was deleted, this check passes, and `on_accept` runs again — minting the beneficiary a second time, then failing again at the same signature check, deleting the receipt again. This can be repeated as many times as an attacker wants to resubmit the message (which is itself an unprivileged, permissionless action — the "relayer" role in this handler is not privileged/trusted, it is merely whoever submits the message with a valid proof).

### Impact Explanation
An unprivileged actor who controls the `data` field of their own outbound `Message` (via `send()`/`SendParams.call_data`, fully attacker-controlled, no validation on the source side) can craft calldata with a corrupted `signature` field. Each resubmission of the same commitment mints tokens to the beneficiary again without any corresponding new burn/lock on the source chain, producing unlimited double-credit of bridged assets — a direct violation of "bridged assets ... must move exactly once and only to the rightful beneficiary and amount."

### Likelihood Explanation
High. The trigger requires only: (1) a registered asset/contract mapping (normal production configuration), (2) non-empty `data` with calldata that decodes to `SubstrateCalldata` but fails signature decode/verification, and (3) resubmission of the identical proven request through the standard, permissionless request-handling extrinsic. No relayer collusion, consensus forgery, or privileged operation is needed — only that the same request commitment be submitted more than once, which the code explicitly permits after a failed `on_accept`.

### Recommendation
Reorder `on_accept` so all fallible validation (decode of `Message`, `SubstrateCalldata`, `MultiSignature`, signature/ECDSA verification, `RuntimeCall` decode, `BaseCallFilter` check) happens **before** any mint/transfer of funds. Only after all validation succeeds should the beneficiary be credited and the optional call dispatched. Alternatively, wrap the entire `on_accept` body execution in a storage transaction (`frame_support::storage::with_transaction`) that reverts all storage effects, including the mint, whenever the function returns `Err`.

### Proof of Concept
1. Register an asset/contract mapping as in `should_receive_asset_with_calldata` test setup.
2. Construct a `Message` with valid `to`/`amount` but `data` = `SubstrateCalldata { signature: Some(garbage_bytes_that_fail_MultiSignature_decode), runtime_call: valid_encoded_call }.encode()`.
3. Call `on_accept(post.clone())` — observe `beneficiary` balance increases at lines 93-117, then `Err(HftError::SignatureDecodeError)` is returned at lines 125-126, mirroring the pattern already exercised (successful path) in [8](#0-7) .
4. Simulate the ISMP core wrapper: assert `host.request_receipt(&req)` is absent (deleted per [9](#0-8) ).
5. Call `on_accept(post)` again with the same `post` — balance increases a second time despite no new funds having been locked/burned on the source chain, confirming double-credit.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L93-126)
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

		// Execute optional calldata
		if !message.data.is_empty() {
			let substrate_data = SubstrateCalldata::decode(&mut &message.data[..])
				.map_err(HftError::CalldataDecodeError)?;

			let origin = if let Some(signature) = substrate_data.signature {
				let multi_signature = MultiSignature::decode(&mut &*signature)
					.map_err(HftError::SignatureDecodeError)?;
```

**File:** modules/ismp/core/src/handlers/request.rs (L55-60)
```rust
	for req in msg.requests.iter() {
		let req = Request::Post(req.clone());
		// If a receipt exists for any request then it's a duplicate and it is not dispatched
		if host.request_receipt(&req).is_some() {
			Err(Error::DuplicateRequest { meta: req.clone().into() })?
		}
```

**File:** modules/ismp/core/src/handlers/request.rs (L95-134)
```rust
	let mut total_weights = Weight::zero();
	let result = msg
		.requests
		.into_iter()
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

	Ok(MessageResult::Request { events: result, weight: total_weights })
```

**File:** docs/content/protocol/ismp/requests.mdx (L111-113)
```text
<Callout title={'Danger'} type={"warn"}>
It's important to note that if the `IsmpModule::on_accept` does not return `Ok`, the receipt of this request will not be persisted, allowing the request to be **replayed**. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying it's internal state to prevent partial state changes that could result in critical vulnerabilities in their request handler. This model ensures that if a request cannot be executed successfully on a destination state machine, it can time out gracefully on the source.
</Callout>
```

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L290-321)
```rust
		let module = HyperFungibleToken::default();
		let post = PostRequest {
			source: StateMachine::Evm(1),
			dest: StateMachine::Kusama(100),
			nonce: 0,
			from: hft_contract(),
			to: pallet_hyper_fungible_token::PALLET_ID.to_bytes(),
			timeout_timestamp: 1000,
			body: {
				let msg = Message {
					from: alloy_primitives::Bytes::from(vec![0x11u8; 20]),
					to: alloy_primitives::Bytes::from(beneficiary_bytes.to_vec()),
					amount: {
						let bytes = convert_to_erc20(SEND_AMOUNT, 18, 10).to_big_endian();
						alloy_primitives::U256::from_be_bytes(bytes)
					},
					data: alloy_primitives::Bytes::from(substrate_data.encode()),
				};
				Message::abi_encode(&msg)
			},
		};

		module.on_accept(post.clone()).unwrap();

		// The calldata transferred tokens from beneficiary to final_recipient
		let final_balance = pallet_balances::Pallet::<Test>::free_balance(final_recipient);
		assert_eq!(final_balance, SEND_AMOUNT);

		// Replay should fail (nonce incremented)
		let result = module.on_accept(post);
		assert!(result.is_err());
	});
```
