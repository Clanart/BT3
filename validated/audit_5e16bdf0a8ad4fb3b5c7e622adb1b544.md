### Title
Timed-out cross-chain transfers become permanently unrecoverable after `update_token` rotates or removes a chain's contract mapping - (File: `modules/pallets/hyper-fungible-token/src/lib.rs` / `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
The `hyper-fungible-token` pallet's timeout-refund path (`on_timeout`) resolves the asset to refund purely by reverse-looking up `ContractToAsset::<T>::get(dest, &to)`. The `update_token` extrinsic, however, deletes that same reverse mapping whenever a chain's contract address is rotated (`add_chains`) or a chain is dropped (`remove_chains`), with no dependency on whether any requests dispatched under the old mapping are still in flight. Any `send()` that escrowed/burned a user's funds before the update, and later times out after the update, can no longer be resolved by `on_timeout`, permanently trapping the user's tokens.

### Finding Description
`send()` locks (native) or burns (non-native) a user's tokens and dispatches an ISMP `PostRequest` whose `to` field is the currently-configured EVM contract address for `(destination, asset_id)`: [1](#0-0) 

`update_token` is the only way to change per-chain configuration for an asset. When rotating a chain's contract (`add_chains`) it removes the *old* `ContractToAsset` entry before inserting the new one; when dropping a chain (`remove_chains`) it removes `TokenContracts`, `Precisions`, and the `ContractToAsset` reverse entry outright: [2](#0-1) 

`on_timeout` is the only recovery path for a request that never lands on the destination. It re-derives the asset solely from `ContractToAsset::<T>::get(dest, &to)`, where `to` comes from the original, already-dispatched `PostRequest` (i.e., the contract address that was configured *at send time*, not the current one): [3](#0-2) 

If `update_token` runs (rotating the contract for that chain, or removing the chain) any time between a user's `send()` and the eventual timeout of that request, the `ContractToAsset` entry keyed by the *old* `(dest, to)` pair has already been deleted. `on_timeout` then returns `HftError::UnknownContractOnTimeout` and the refund reverts — the escrowed native tokens sitting in `Pallet::<T>::pallet_account()` (or the burned non-native supply) can never be released back to the sender, because there is no alternate path to resolve the asset for that pending request. The same reasoning applies to the `Precisions` lookup right after (`DecimalsNotConfigured`), which is also removed by `update_token`.

This is the same root cause pattern as the reported bug: a "de-registration" style config change (`update_token` removing/rotating a chain mapping) leaves a downstream execution path (`on_timeout`) trusting stale keying data with no validation that in-flight operations against the old configuration are still resolvable, resulting in an unrecoverable state for user funds.

### Impact Explanation
Users' escrowed native tokens or burned non-native tokens become permanently locked with no code path to reclaim them once the corresponding `ContractToAsset` entry is removed. This is a direct, protocol-level loss/lock of user funds triggered by a completely ordinary operational action (contract address migration or chain deprecation) — not a malicious admin, malicious relayer, or compromised key. Given that chain migrations and address rotations are expected lifecycle events for a bridge, this is a realistic and repeatable fund-loss scenario, matching the bounty's "stealing or loss of funds" / "logic attacks" impact classes.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: `update_token` is an expected maintenance operation (e.g., migrating to a new `HyperFungibleToken` contract deployment, or deprecating support for a chain), and ISMP request timeouts routinely occur due to relayer failure, congestion, or intentional cancellation. Any overlap between an in-flight `send()` and a routine `update_token` call for the same `(chain, asset)` pair triggers the bug — no attacker coordination, front-running, or privileged-actor malice is required, only ordinary timing.

### Recommendation
Do not rely solely on the *current* `ContractToAsset`/`Precisions` mappings to resolve in-flight/timed-out requests. Either:
- Snapshot the asset id and decimals into the dispatched request body itself (so `on_timeout` never needs to look up mutable pallet storage), or
- Keep a append-only/history-preserving mapping (e.g., versioned or never-deleted `ContractToAsset` entries) so historical contract addresses remain resolvable for pending requests until they've timed out or been delivered, or
- Track outstanding in-flight commitments per `(chain, asset, contract)` and block `update_token` from removing a mapping while pending requests still reference it.

### Proof of Concept
1. Governance registers asset `X` as non-native with `TokenContracts[Evm(1)][X] = ContractA`.
2. Alice calls `send()` for asset `X` to `Evm(1)`; her tokens are burned, and an ISMP `PostRequest{ to: ContractA, dest: Evm(1), ... }` is dispatched with a timeout.
3. Before the request is delivered or times out, governance calls `update_token` to rotate the contract: `add_chains = { Evm(1): ContractB }`. This executes:
   `ContractToAsset::<T>::remove(Evm(1), ContractA)` then inserts `ContractToAsset::<T>::insert(Evm(1), ContractB, X)`
   (`modules/pallets/hyper-fungible-token/src/lib.rs:408-419`).
4. The request never gets delivered (relayer outage) and times out. `pallet-ismp` invokes `on_timeout` with the original `PostRequest{ to: ContractA, dest: Evm(1) }`.
5. `on_timeout` executes `ContractToAsset::<T>::get(Evm(1), ContractA)` → `None` → returns `Err(HftError::UnknownContractOnTimeout)`
   (`modules/pallets/hyper-fungible-token/src/module.rs:236-237`).
6. The timeout dispatch fails; Alice's burned tokens are never refunded and there is no other extrinsic to recover them — permanent fund loss.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-310)
```rust
			let token_contract =
				TokenContracts::<T>::get(params.destination, params.asset_id.clone())
					.ok_or(Error::<T>::TokenContractNotFound)?;
			let erc_decimals = Precisions::<T>::get(params.asset_id.clone(), params.destination)
				.ok_or(Error::<T>::DecimalsNotFound)?;

			// Lock or burn the local asset
			let decimals = if params.asset_id == T::NativeAssetId::get() {
				// escrow the native asset
				<T as Config>::NativeCurrency::transfer(
					&who,
					&Self::pallet_account(),
					params.amount,
					ExistenceRequirement::AllowDeath,
				)?;
				T::Decimals::get()
			} else {
				let is_native = NativeAssets::<T>::get(params.asset_id.clone());
				if is_native {
					<T as Config>::Assets::transfer(
						params.asset_id.clone(),
						&who,
						&Self::pallet_account(),
						params.amount.into(),
						Preservation::Expendable,
					)?;
				} else {
					<T as Config>::Assets::burn_from(
						params.asset_id.clone(),
						&who,
						params.amount.into(),
						Preservation::Expendable,
						Precision::Exact,
						Fortitude::Polite,
					)?;
				}
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
			};

			let dispatch_post = DispatchPost {
				dest: params.destination,
				from: PALLET_ID.to_bytes(),
				to: token_contract,
				timeout: params.timeout,
				body: Message::abi_encode(&token_message),
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L398-430)
```rust
			for (chain, config) in update.add_chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
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
			}

			for chain in update.remove_chains {
				if let Some(old_contract) = TokenContracts::<T>::get(chain, update.asset_id.clone())
				{
					ContractToAsset::<T>::remove(chain, old_contract);
				}
				TokenContracts::<T>::remove(chain, update.asset_id.clone());
				Precisions::<T>::remove(update.asset_id.clone(), chain);
			}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L218-247)
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
```
