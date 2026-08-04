## Analysis

The Vader bug's core invariant break is: **a shared mapping key (`foreignAsset`) can be silently reused/overwritten by a later registration, changing the resolved value for an earlier, unrelated entry.** The direct analog in Hyperbridge lives in the `hyper-fungible-token` pallet's registration path.

`register_token` writes both `TokenContracts` (asset → contract) and its reverse lookup `ContractToAsset` (contract → asset) without any check that the `(chain, token_contract)` key isn't already claimed by a *different* `AssetId`: [1](#0-0) 

Contrast this with `update_token`, which explicitly removes the stale reverse-mapping entry before writing a new one — but only for the *same* `asset_id` being updated: [2](#0-1) 

Neither call path checks whether `ContractToAsset[chain][token_contract]` already resolves to a *different* asset before overwriting it. `on_accept` trusts this reverse lookup blindly to decide which asset to mint/transfer for an incoming cross-chain message: [3](#0-2) 

### Title
Registering a second token with a colliding `(StateMachine, contract)` key silently hijacks the `ContractToAsset` routing of an earlier registered asset - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`ContractToAsset` is keyed by `(StateMachine, contract_address_bytes)`, mirroring the Vader bug's `oracles[foreignAsset]` pattern where multiple distinct entities (asset registrations here, trading pairs there) share one mapping slot keyed on a single dimension instead of the full logical pair. `register_token` inserts into this reverse map unconditionally, with no guard against overwriting an entry that already belongs to a different, live `AssetId`.

### Finding Description
`TokenContracts<T>` maps `(StateMachine, AssetId) -> contract bytes` and is used for outbound dispatch, while `ContractToAsset<T>` maps `(StateMachine, contract bytes) -> AssetId` and is the sole authentication check used in `on_accept` to decide which local asset an incoming message credits: `ContractToAsset::<T>::get(source, &from).ok_or(HftError::UnknownSourceContract(source))?` [4](#0-3) .

In `register_token`, for every `(chain, config)` supplied by `CreateOrigin`, the pallet inserts into both maps without first checking whether `ContractToAsset::<T>::get(chain, &token_contract)` already exists and resolves to a *different* `AssetId`: [5](#0-4) .

If a new token is registered whose `config.token_contract` for some `chain` matches an already-registered contract address belonging to an older asset (e.g. an operator error, a redeployed/reused proxy address, or a re-registration of the same token under a new `AssetId` by mistake), `ContractToAsset[chain][contract]` is silently rewritten to point at the new asset. The old asset's `TokenContracts[chain][old_asset_id]` entry is untouched and keeps dispatching outbound transfers to that same contract address, but any subsequent inbound message from that contract is now authenticated and credited as the *new* asset instead of the old one. This is exactly the Vader pattern: adding a new "pair" (asset registration) unexpectedly replaces the resolved value (asset binding) of an older, unrelated entry that shares the same underlying key.

### Impact Explanation
Once the collision occurs, every legitimate incoming transfer originating from the affected EVM contract is routed through `on_accept` and minted/transferred as the wrong `AssetId` and with the wrong `Precisions` lookup (which is also keyed by asset, not contract) [6](#0-5) . This can mint/transfer the wrong fungible asset to beneficiaries with mismatched decimal scaling, silently misroute funds intended for the original asset's escrow/mint-burn accounting into a different asset's accounting, and break the outbound/inbound symmetry for the original asset (`send` still targets the old contract via `TokenContracts`, but `on_accept` no longer recognizes it under the old asset). This is a false-state-acceptance / wrong-beneficiary-asset condition with direct fund-crediting consequences, matching the bounty's "false proof/state acceptance" and "unauthorized transaction or execution" categories.

### Likelihood Explanation
Triggering the collision requires a `CreateOrigin` call to `register_token`, but no malicious governance intent is needed — any operator error that reuses or duplicates a `token_contract` address across two `AssetId`s (e.g. copy-paste of chain configs, re-registering a token under a new ID during a migration) triggers it. Once the mapping is corrupted, exploitation of the resulting mis-crediting requires zero privilege: it happens automatically for every ordinary incoming `on_accept` call from the affected contract, exactly as with the Vader oracle overwrite, where any later addition of a matching-`foreignAsset` pair silently altered behavior for an earlier one.

### Recommendation
Bind `ContractToAsset` inserts to a uniqueness check: before insertion, look up `ContractToAsset::<T>::get(chain, &token_contract)` and reject the call (`Error::<T>::ContractAlreadyRegisteredToAnotherAsset` or similar) if it resolves to a different `AssetId` than the one being registered/updated, in both `register_token` and `update_token`. Optionally also index/validate uniqueness of `TokenContracts` values within a chain to prevent the same contract address being assigned to two different assets in the first place.

### Proof of Concept
1. Governance calls `register_token` for `asset_id = A` with `chains = { EVM(1): { token_contract: 0xCCCC..., decimals: 18 } }`. This sets `TokenContracts[EVM(1)][A] = 0xCCCC` and `ContractToAsset[EVM(1)][0xCCCC] = A`.
2. Later, governance calls `register_token` for a new `asset_id = B` and, due to a config error or address reuse, again supplies `chains = { EVM(1): { token_contract: 0xCCCC..., decimals: 6 } }`. No check exists at [5](#0-4)  to prevent this; `ContractToAsset[EVM(1)][0xCCCC]` is overwritten to `B`, while `TokenContracts[EVM(1)][A]` still equals `0xCCCC`.
3. A user sends asset `A` cross-chain via `send`, which dispatches to `to = TokenContracts[EVM(1)][A] = 0xCCCC` as before [7](#0-6) .
4. Any subsequent legitimate inbound `PostRequest` from `source = EVM(1), from = 0xCCCC` (whether the original A-related traffic, or a genuine B transfer) is authenticated via `ContractToAsset::get(EVM(1), 0xCCCC) = B` and minted/transferred as asset `B` with `B`'s decimals from `Precisions<T>` [8](#0-7) , silently misrouting value between the two assets' accounting without any privileged or malicious actor being required for the exploitation step.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L304-310)
```rust
			let dispatch_post = DispatchPost {
				dest: params.destination,
				from: PALLET_ID.to_bytes(),
				to: token_contract,
				timeout: params.timeout,
				body: Message::abi_encode(&token_message),
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L356-368)
```rust
				let token_contract = config.token_contract.0.to_vec();
				TokenContracts::<T>::insert(
					chain,
					registration.local_id.clone(),
					token_contract.clone(),
				);
				ContractToAsset::<T>::insert(
					chain,
					token_contract,
					registration.local_id.clone(),
				);
				Precisions::<T>::insert(registration.local_id.clone(), chain, config.decimals);
			}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L407-420)
```rust
				// Remove old reverse mapping if it exists
				if let Some(old_contract) = TokenContracts::<T>::get(chain, update.asset_id.clone())
				{
					ContractToAsset::<T>::remove(chain, old_contract);
				}

				let token_contract = config.token_contract.0.to_vec();
				TokenContracts::<T>::insert(
					chain,
					update.asset_id.clone(),
					token_contract.clone(),
				);
				ContractToAsset::<T>::insert(chain, token_contract, update.asset_id.clone());
				Precisions::<T>::insert(update.asset_id.clone(), chain, config.decimals);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-91)
```rust
	fn on_accept(
		&self,
		PostRequest { body, from, source, .. }: PostRequest,
	) -> Result<Weight, anyhow::Error> {
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;

		// Decode the Message
		let message = Message::abi_decode(&body).map_err(HftError::DecodeError)?;

		// Convert recipient bytes to substrate AccountId
		// If 32 bytes: use directly. If 20 bytes: left-pad with zeros.
		let mut beneficiary_bytes = [0u8; 32];
		let to_bytes = message.to.as_ref();
		if to_bytes.len() == 32 {
			beneficiary_bytes.copy_from_slice(to_bytes);
		} else if to_bytes.len() == 20 {
			beneficiary_bytes[12..].copy_from_slice(to_bytes);
		} else {
			Err(HftError::InvalidRecipientLength(to_bytes.len()))?;
		}
		let beneficiary: T::AccountId = beneficiary_bytes.into();

		// Convert amount from ERC20 denomination to local
		let decimals = if local_asset_id == T::NativeAssetId::get() {
			T::Decimals::get()
		} else {
			<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
				local_asset_id.clone(),
			)
		};
		let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), source)
			.ok_or(HftError::DecimalsNotConfigured(source))?;
		let amount = convert_to_balance::<
			<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
		>(
			U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
			erc_decimals,
			decimals,
		)
		.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```
