### Title
Precision update for an in-flight `pallet-hyper-fungible-token` asset corrupts refund/receive accounting, draining the shared escrow pool - ([File: modules/pallets/hyper-fungible-token/src/module.rs])

### Summary
`pallet-hyper-fungible-token` lets `CreateOrigin` change a token's decimal `Precisions` for a chain via `update_token` at any time, with no check that pending (in-flight) cross-chain requests for that asset have been settled first. Because the amount encoded in an outgoing ISMP message is only stored as a raw ERC20 `U256` (no source-side decimals snapshot), and `on_accept`/`on_timeout` re-derive the local amount using whatever `Precisions` value exists *at settlement time*, a precision change that lands between `send()` and the corresponding `on_accept`/`on_timeout` desynchronizes the amount that was actually escrowed from the amount that gets paid out of the shared `pallet_account()`. This is a direct structural analog of the NTE-2 report: a critical parameter that determines "how much backing corresponds to this locked value" is mutable post-mint/post-lock, with no pause and no fund migration, so some holders' claims end up unbacked while others may over-drain the shared custody pool.

### Finding Description
`send()` escrows/burns the local amount and encodes the destination amount by reading `Precisions::<T>::get(asset_id, destination)` and the local asset decimals at that instant: [1](#0-0) 

This computed `erc20_amount` (a raw `U256`) is the *only* thing placed in the outgoing `Message` — there is no snapshot of which `erc_decimals`/`decimals` pair produced it: [2](#0-1) 

`update_token`, callable at any time by `CreateOrigin` with no in-flight-request check or pause, overwrites the `Precisions` storage entry for `(asset_id, chain)`: [3](#0-2) 

Both settlement paths — `on_accept` (successful delivery, mints/unlocks to beneficiary) and `on_timeout` (refund, unlocks back to original sender) — re-read `Precisions::<T>::get(local_asset_id, source/dest)` **at settlement time** and reconvert the raw message amount using whatever decimals are configured *then*, not the decimals in effect when the funds were originally locked: [4](#0-3) [5](#0-4) 

If `Precisions` for `(asset_id, chain)` changes between when a request is dispatched and when it is later accepted or times out, `convert_to_balance` no longer inverts the `convert_to_erc20` used at send time. The payout (`amount`) transferred out of the shared `Pallet::<T>::pallet_account()` custody account will not equal the amount that was actually escrowed for that specific request. Since the custody account backs *all* users' pending transfers for that asset, an over-conversion for one request drains principal that belongs to other users' still-pending requests, while an under-conversion locks funds permanently for the affected sender — precisely the "shifted to a different redemption basis with insufficient backing" failure mode from the report, just expressed through decimal precision instead of an `ethereum` boolean.

### Impact Explanation
The shared `pallet_account()` custody balance backs every in-flight `send()` for a given native/native-escrowed asset. A single precision change while requests are outstanding can cause `on_timeout`/`on_accept` to pay out an incorrect amount from that shared pool for any of those pending requests, which:
- can pay a refund/receipt larger than what was actually escrowed for that message, draining collateral that belongs to other users' still-pending requests, and
- can also short-change the affected sender if precision moves the other direction.

Either way this results in loss of funds for some Ethernote-analog holders (users with pending sends) — a direct fund-loss/wrong-amount impact matching the bounty's "stealing or loss of funds" and "logic attacks" categories.

### Likelihood Explanation
No malicious relayer, prover, or leaked key is required. The only precondition is that `update_token` (a normal, documented, non-emergency-gated operation — e.g. correcting a decimals config or reconfiguring a chain) executes while any `send()` for that `(asset_id, chain)` is still outstanding (undelivered and not yet timed out). Given `update_token` has no invariant preventing this and no pause of in-flight transfers, and cross-chain message delivery/timeout windows are non-trivial (seconds to the full `timeout` value), this is readily triggerable in normal operation, not merely a theoretical edge case.

### Recommendation
- Snapshot the source-side `erc_decimals`/local `decimals` pair (or equivalently the final `local_amount`) into the outgoing/incoming request state (e.g. as part of the dispatched commitment metadata) at `send()` time, and use that snapshot — not the live `Precisions` value — in `on_accept`/`on_timeout` to compute payout amounts.
- Alternatively, require `update_token` to be a two-step process: mark the old precision inactive, drain/settle (or migrate) all outstanding requests referencing it, and only then activate the new precision — mirroring the report's recommendation to only update the critical parameter while new activity referencing it is paused and to shift funds/backing consistently.
- Add an event/replay-safe versioning scheme so historical `Precisions` values remain queryable for requests dispatched under them.

### Proof of Concept
1. Asset `X` is native-escrowed with `Precisions::<T>::get(X, Evm(1)) == 18`.
2. User A calls `send(asset_id: X, destination: Evm(1), amount: 100)`. This locks `100` of local balance into `pallet_account()` and encodes `erc20_amount = convert_to_erc20(100, 18, local_decimals)` in the outgoing `Message`, per `send()` at `modules/pallets/hyper-fungible-token/src/lib.rs:257-295`.
3. Before the request is delivered or times out, `CreateOrigin` calls `update_token` to change the chain's decimals for asset `X` to, say, `6` (a legitimate-looking reconfiguration; no check blocks this while A's request is pending), per `update_token` at `modules/pallets/hyper-fungible-token/src/lib.rs:384-421`.
4. The request later times out. `on_timeout` looks up the *current* `Precisions::<T>::get(X, Evm(1)) == 6` and recomputes `amount = convert_to_balance(message.amount, 6, local_decimals)`, per `modules/pallets/hyper-fungible-token/src/module.rs:239-285`. Because the ERC20 amount was encoded assuming 18 decimals but is now decoded assuming 6 decimals, the recomputed local `amount` differs from the `100` originally escrowed by a factor of `10^12`, causing either a massive over-refund (draining `pallet_account()` funds belonging to other pending senders) or an under-refund (permanent partial loss for A), depending on rounding direction of `convert_to_balance`. [3](#0-2) [6](#0-5)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-295)
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
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L384-421)
```rust
		pub fn update_token(
			origin: OriginFor<T>,
			update: TokenUpdate<AssetId<T>>,
		) -> DispatchResult {
			T::CreateOrigin::ensure_origin(origin)?;

			let local_decimals = if update.asset_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					update.asset_id.clone(),
				)
			};

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
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L39-59)
```rust
/// Converts an ERC20 U256 amount to a local balance type
///
/// Divides by 10^(erc_decimals - local_decimals) to scale down from ERC20 precision.
/// The target type must implement `FromStr`.
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

/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L74-91)
```rust
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
