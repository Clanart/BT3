## Analysis

The Connext bug (`setWrapper` not cascading to `canonicalToAdopted`) is a stale-mapping class bug: an admin function meant to update one address leaves a *dependent* mapping pointing at the old address, and that dependent mapping is later trusted for authorization/execution. Hyperbridge's `pallet-hyper-fungible-token` has a direct structural analog in `register_token` vs. `update_token`.

### Title
Stale `ContractToAsset` reverse-mapping in `register_token` allows a deregistered/replaced EVM contract to remain a trusted message source - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`register_token` writes both `TokenContracts` (asset→contract) and `ContractToAsset` (contract→asset) but never removes the *old* `ContractToAsset` entry when it is called again for an asset/chain pair that is already registered with a different contract address. `update_token`, the pallet's dedicated "change config" call, correctly clears the stale reverse mapping before writing the new one, proving the maintainers are aware this cleanup is required — but `register_token` is missing the same guard.

### Finding Description
In `register_token`: [1](#0-0) 
the old contract address is never looked up or removed from `ContractToAsset` before the new one is inserted. Compare this with `update_token`, which explicitly does the cleanup: [2](#0-1) 

`ContractToAsset` is the sole authentication mechanism for incoming cross-chain messages and for timeout refunds: [3](#0-2) [4](#0-3) 

If `register_token` is invoked a second time for the same `local_id`/chain to point at a new contract address (e.g. migrating the EVM-side token contract, which is the realistic maintenance path since `register_token` — unlike `update_token` — is also the only call that can set `NativeAssets` and re-validate `local_decimals`), the old contract address `A` is silently left as a valid entry in `ContractToAsset::<T>::get(chain, A) == asset_id`, alongside the new contract `B`. Both `A` and `B` are now treated as authenticated sources of `Message`s for the same local asset.

### Impact Explanation
`on_accept` uses `ContractToAsset` purely as an identity/module binding check before minting or transferring funds to a beneficiary: [5](#0-4) 
Because the old contract address is never revoked, any ISMP post request whose `from` field equals the stale (deregistered/replaced) contract address `A` is still accepted as legitimate for that asset — even though the admin's intent (by re-registering) was to stop trusting `A`. This is exactly the "false module binding" class flagged in the pivots: cross-chain minting occurs based on a corrupted/stale value (`ContractToAsset[chain][A]`) that existing guards (the `ok_or(HftError::UnknownSourceContract)` check) do not catch, since the guard only checks presence, not staleness. If contract `A` is later redeployed, compromised, or was intentionally deprecated because of a bug, it can still originate mint requests, leading to unauthorized minting of the bridged asset to attacker-controlled beneficiaries — a direct fund-creation/loss vector matching the bounty's "false proof/state acceptance" and "unauthorized transaction or execution" categories.

### Likelihood Explanation
This does not require a malicious admin, relayer, or prover. It is triggered by an entirely ordinary, sanctioned administrative action — re-running `register_token` (rather than `update_token`) to point an existing asset at a new contract address, which is plausible since `register_token` is the only call capable of setting `native`/`NativeAssets` and is not documented as single-use. Once that stale entry exists, exploitation only requires a normal, unprivileged ISMP relayer delivering a message from the old (still-technically-valid) `from` address — no compromised keys or governance takeover needed.

### Recommendation
In `register_token`, before inserting into `TokenContracts`/`ContractToAsset`, look up any existing `TokenContracts::<T>::get(chain, local_id)` and, if a different contract address is already stored, remove the old `ContractToAsset` entry first — mirroring the cleanup already implemented in `update_token`. Ideally, unify the two code paths (have `register_token` delegate to the same per-chain "set contract" helper used by `update_token`) so this invariant can't drift again.

### Proof of Concept
1. Admin calls `register_token` with `local_id = X`, chain `Evm(1)`, `token_contract = A`. Now `TokenContracts[Evm(1)][X] = A` and `ContractToAsset[Evm(1)][A] = X`.
2. Admin later calls `register_token` again for the same `local_id = X`, chain `Evm(1)`, but with `token_contract = B` (e.g., migrating to a redeployed/fixed contract). Now `TokenContracts[Evm(1)][X] = B` and `ContractToAsset[Evm(1)][B] = X`, but `ContractToAsset[Evm(1)][A] = X` is **still present** (never removed).
3. Anyone able to get an ISMP post request delivered with `source = Evm(1)`, `from = A` (e.g., contract `A` is later redeployed via `CREATE2`/`SELFDESTRUCT` cycle by an attacker, or `A` was already flagged as buggy/compromised, which is why it was replaced) will pass the `ContractToAsset::get(source, &from)` check in `on_accept` and mint/transfer asset `X` to an arbitrary `beneficiary`, even though the admin's re-registration was intended to revoke trust in `A`. [6](#0-5) [7](#0-6)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L327-376)
```rust
		/// Registers a new token with per-chain contract configuration
		#[pallet::call_index(1)]
		#[pallet::weight(T::WeightInfo::register_token(registration.chains.len() as u32))]
		pub fn register_token(
			origin: OriginFor<T>,
			registration: TokenRegistration<AssetId<T>>,
		) -> DispatchResult {
			T::CreateOrigin::ensure_origin(origin)?;

			let local_decimals = if registration.local_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					registration.local_id.clone(),
				)
			};

			NativeAssets::<T>::insert(registration.local_id.clone(), registration.native);

			let chains: Vec<StateMachine> = registration.chains.keys().cloned().collect();
			for (chain, config) in registration.chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
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

			Self::deposit_event(Event::<T>::TokenRegistered {
				asset_id: registration.local_id,
				native: registration.native,
				chains,
			});
			Ok(())
		}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L407-419)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-56)
```rust
	fn on_accept(
		&self,
		PostRequest { body, from, source, .. }: PostRequest,
	) -> Result<Weight, anyhow::Error> {
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L93-116)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L235-237)
```rust
				// Look up the asset from the destination contract address
				let local_asset_id = ContractToAsset::<T>::get(dest, &to)
					.ok_or(HftError::UnknownContractOnTimeout)?;
```
