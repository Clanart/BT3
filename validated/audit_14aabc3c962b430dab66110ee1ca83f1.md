### Title
Live re-fetch of asset `decimals()` in `on_timeout` (and `on_accept`) allows the token owner to corrupt refund/mint amounts via metadata drift - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`send()` bakes the cross-chain ERC20 amount using the local asset's `decimals()` sampled at send time, but `on_timeout` (and `on_accept`) re-derive `local_decimals` by calling `fungibles::metadata::Inspect::decimals()` again at execution time instead of using the value that was in effect when the message was created. For non-native assets registered in this pallet, `decimals()` is mutable metadata that the asset's owner can update via `pallet_assets::set_metadata` — an unprivileged, asset-owner-scoped call, not a runtime governance action. Changing decimals between `send` and the timeout callback breaks the round-trip conversion and lets the refund amount diverge from what was actually escrowed, draining the pallet's shared custody account (`pallet_account`) at the expense of other users of the same asset.

### Finding Description
In `send()`, the pallet:
1. Escrows/burns `params.amount` (local denomination) from the sender.
2. Reads `decimals` live from `<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(asset_id)`.
3. Computes `erc20_amount = convert_to_erc20(amount, erc_decimals, decimals)` and embeds that fixed `erc20_amount` into the outbound `Message` body. [1](#0-0) 

When the request later times out, `on_timeout` decodes the *same, unchanged* `message.amount` (the ERC20-denominated value fixed at send time) but re-fetches `decimals` live again: [2](#0-1) 

and uses it to compute the local refund via `convert_to_balance`, which divides by `10^(erc_decimals - local_decimals)`: [3](#0-2) 

If `local_decimals` at timeout differs from `local_decimals` at send time, the round trip `local -> erc20 -> local` is no longer the identity function:
- If the asset owner *increases* the reported decimals between send and timeout, `erc_decimals.saturating_sub(local_decimals)` shrinks, the divisor shrinks, and the computed refund is **larger** than what was originally escrowed for that message.
- If decimals are decreased, the refund is smaller, silently under-refunding the sender.

Because `local_decimals` is obtained by calling the asset's own metadata inspector rather than a value snapshotted in the message or in `Precisions`/registration state, and because `pallet_assets::set_metadata` (or an equivalent owner-controlled metadata call) can be invoked by the account that created/owns that specific asset — a normal, unprivileged action, not root/governance — the pallet's registration step (`register_token`/`update_token`) only pins `erc_decimals` (the remote precision), never the local asset's own decimals. The pallet accounting therefore implicitly and incorrectly assumes local asset decimals are immutable for the lifetime of an in-flight message.

Since refunds are paid from the shared `pallet_account()` custodial pool (for `is_native` assets) rather than from a per-message locked amount, an inflated refund for one message drains the shared pool, at the expense of other users' pending escrows for the same asset — this is not merely self-inflicted loss to the attacker.

### Impact Explanation
An unprivileged asset owner can:
1. Register (or have registered) a `pallet-assets` asset with the bridge via `register_token`.
2. Send bridge messages via `send()`, escrowing tokens into `pallet_account()`.
3. Before the timeout callback executes, call `set_metadata` to raise the asset's `decimals` value.
4. Trigger/await the timeout path, at which point `on_timeout` computes an inflated refund amount and transfers more tokens out of `pallet_account()` than were escrowed for that message — potentially exceeding the attacker's own escrowed balance and consuming funds escrowed by other users of the same asset.

This directly matches the "broken timeout/refund accounting" and "wrongful asset movement/amount" impact categories in scope, and can drain shared custody funds belonging to unrelated users.

### Likelihood Explanation
- Requires the attacker to control (own) the asset that was registered with the bridge; `pallet_assets::create`/asset ownership acquisition is typically permissionless, and `set_metadata` is callable by the asset owner without further privilege.
- Requires a timeout to occur (attacker can pick a very short `timeout` in `SendParams` to make this reliable/self-triggerable) or wait for a natural timeout.
- No proof forgery, relayer collusion, or governance access is needed — only ordinary metadata mutation on an asset the attacker legitimately owns, combined with the existing `send`/timeout flow.

### Recommendation
Do not re-derive `local_decimals` at settlement time. Instead:
- Snapshot the local asset's decimals at `send()` time and either encode it into the outbound message (or a companion pending-request storage item keyed by commitment) so that `on_timeout`/`on_response` use the exact same decimals value used to compute the original escrow, or
- Store the escrowed local amount (not just the ERC20 amount) per commitment at dispatch time and refund that exact stored amount on timeout instead of recomputing it via `convert_to_balance`.

Apply the same fix to `on_accept`'s decimals lookup path if the corresponding registration/decimals value can drift for a pending in-flight message. Additionally, consider locking/caching decimals at asset-registration time and rejecting registrations for assets whose owner can still mutate decimals after registration, or requiring decimals-immutability enforcement.

### Proof of Concept
1. Register a non-native asset `X` (owned by `attacker`) with the bridge via `register_token`, with `erc_decimals = 18` and asset `decimals() = 6` at registration time.
2. `attacker` calls `send()` with `amount = 1_000_000` (i.e., 1.0 token at 6 decimals), escrowing/burning that amount; `erc20_amount` is computed and fixed in the message body as `1_000_000 * 10^(18-6) = 1e18`.
3. Set a short timeout for the request.
4. Before the timeout callback fires, `attacker` (as owner of asset `X`) calls `pallet_assets::set_metadata` to change `X`'s decimals to `9`.
5. Timeout fires; `on_timeout` re-reads `decimals()` for asset `X`, now `9`, and computes `convert_to_balance(1e18, 18, 9) = 1e18 / 10^(18-9) = 1_000_000_000` — a refund of `1,000,000,000` units instead of the originally escrowed `1,000,000` units, a 1000x inflation, paid out of the shared `pallet_account()`.
6. Assert: refunded amount != originally escrowed amount, and other users' escrowed balances for asset `X` in `pallet_account()` are reduced/drained as a result. [4](#0-3)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L284-300)
```rust
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L236-255)
```rust
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L257-292)
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

				Pallet::<T>::deposit_event(Event::<T>::TokenRefunded {
					beneficiary,
					amount: amount.into(),
					dest,
				});
				Ok(T::DbWeight::get().reads_writes(5, 2))
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-52)
```rust
pub fn convert_to_balance<B: core::str::FromStr>(
	value: U256,
	erc_decimals: u8,
	local_decimals: u8,
) -> Result<B, B::Err> {
	let dec_str = (value /
		U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32)))
	.to_string();
	dec_str.parse::<B>()
}
```
