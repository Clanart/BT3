### Title
Decimal conversion drift from live (unlocked) `local_decimals` reads across `send` / `on_accept` / `on_timeout` — (File: `modules/pallets/hyper-fungible-token/src/module.rs`, `modules/pallets/hyper-fungible-token/src/impls.rs`, `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
The `erc_decimals >= local_decimals` invariant that governs `convert_to_erc20`/`convert_to_balance` is enforced **once**, at `register_token`/`update_token` time, by comparing the configured remote (ERC20) decimals against the *current* live value of `local_decimals` read from `<T::Assets as fungibles::metadata::Inspect>::decimals(asset_id)` [1](#0-0) . That local-decimals value is never snapshotted into `Precisions` or any per-message state — it is re-fetched live from `pallet-assets` metadata on every subsequent `send`, `on_accept`, and `on_timeout` call [2](#0-1) [3](#0-2) [4](#0-3) .

Because asset creation in the runtime's `pallet-assets` is permissionless (`CreateOrigin = AsEnsureOriginWithArg<EnsureSigned<AccountId32>>`) [5](#0-4) , the asset's owner/admin — an ordinary unprivileged account, not the HFT pallet's `CreateOrigin` (`EnsureRoot`) — retains standard `pallet-assets` administrative rights over that asset's metadata, including the ability to change its `decimals` value after the asset has already been registered with the hyper-fungible-token pallet. `register_token`/`update_token` only check `config.decimals >= local_decimals` **at the moment of registration**, against whatever `local_decimals` happens to be at that time [6](#0-5) .

### Finding Description
`convert_to_erc20` and `convert_to_balance` compute a scale factor as `10^(erc_decimals - local_decimals)` and its inverse [7](#0-6) . This is only a correct inverse pair if `local_decimals` is identical at `send` time and at the corresponding `on_accept`/`on_timeout` time for the same logical transfer. The pallet stores `erc_decimals` in the `Precisions` map (frozen at registration) but does **not** freeze `local_decimals` anywhere per-asset or per-message; it always re-reads the live decimals from `pallet-assets` metadata:

- `send`: reads `decimals` live via `fungibles::metadata::Inspect::decimals` right before burning/escrowing and encoding the outbound amount [8](#0-7) .
- `on_accept`: reads `decimals` live again to convert the inbound ERC20 amount back to local balance before minting/releasing [3](#0-2) .
- `on_timeout`: reads `decimals` live yet again to compute the refund amount [4](#0-3) .

If the asset owner (unprivileged, standard `pallet-assets` capability) changes the asset's `decimals` metadata between a `send` and the corresponding `on_timeout` (or between two related `send`/`on_accept` flows for round-tripping assets), the amount burned/escrowed at `send` time (scaled with old decimals) no longer matches the amount computed for refund/receipt (scaled with new decimals). This is exactly the "one step authenticates the old context, a later step consumes the new context" pattern: the registration-time check (`ErcDecimalsBelowLocal`) authenticated the old `local_decimals`, but the actual conversion arithmetic in `send`/`on_accept`/`on_timeout` consumes whatever `local_decimals` is live at call time.

Concretely:
1. Governance registers asset `X` (owned by attacker via permissionless `pallet-assets` creation) with `local_decimals = 6`, remote `erc_decimals = 18` (passes `ErcDecimalsBelowLocal` check).
2. Attacker calls `send` with `amount = 1_000_000` (i.e., 1.0 token at 6 decimals): the pallet burns/escrows `1_000_000` and computes `erc20_amount = 1_000_000 * 10^(18-6) = 10^18` for the outbound message.
3. Before the message times out (or before a return transfer triggers `on_accept`), attacker calls `pallet_assets::set_metadata` to change `X`'s decimals to `0`.
4. On `on_timeout`, the pallet re-reads `local_decimals = 0` and computes `refund = 10^18 / 10^(18-0) = 1` — a massive loss (dust refund) — or, conversely, by shrinking `erc_decimals`-vs-`local_decimals` the other direction, an attacker can inflate the refund/mint far above what was originally escrowed/burned, minting/unlocking more value than was ever locked.

### Impact Explanation
This lets an unprivileged asset owner manipulate the decimal scaling factor used at `on_accept`/`on_timeout` independently of the factor implicitly assumed at `send` time, breaking the "precision conversion must preserve economic value" invariant. Depending on the direction of the decimals change, this can either (a) mint/release/refund far more local tokens than were ever escrowed/burned (drain of the pallet's escrow account or unbounded minting of a non-native representation), or (b) cause honest users' funds to be dramatically under-refunded/under-credited. Case (a) matches "Critical: wrongful mint, unlock, withdrawal, refund ... of protocol-controlled or user escrowed assets."

### Likelihood Explanation
The precondition is that the token being bridged is a `pallet-assets` asset whose `decimals` metadata remains mutable by its creator/owner after being registered with `pallet_hyper_fungible_token`. This is plausible given `pallet-assets`' `CreateOrigin` in this runtime is permissionless (`EnsureSigned`), meaning any asset created and later registered for bridging by governance retains attacker-controlled metadata unless governance/asset design freezes metadata (`freeze_metadata`) before/at registration. I could not verify from the indexed code whether the deployed asset-registration process (off-chain governance process for `register_token`) requires or checks that metadata is frozen prior to registration — this is a real operational gap in the on-chain logic itself, since the pallet never re-validates or snapshots `local_decimals` for the lifetime of an asset. For the chain's own native currency (`T::NativeAssetId`), decimals come from the immutable `T::Decimals::get()` constant, so this specific vector is limited to non-native `pallet-assets` tokens, not the native asset.

### Recommendation
- Snapshot `local_decimals` into per-asset `Precisions`-like frozen storage at `register_token` time (instead of re-reading live metadata on every call), and require any change (`update_token`) to go through the same privileged `CreateOrigin` re-validation against the *current* metadata.
- Alternatively/additionally, require assets to have frozen (`pallet_assets::freeze_metadata`) decimals before they can be registered via `register_token`, and reject `on_accept`/`on_timeout`/`send` if metadata is unfrozen or decimals no longer match the value recorded at registration.
- Add an explicit invariant check at conversion time comparing live decimals against the stored/expected decimals and fail closed (reject the message / refuse to mint) on mismatch, rather than silently using a possibly-changed value.

### Proof of Concept
1. Deploy runtime with `pallet_assets::CreateOrigin = AsEnsureOriginWithArg<EnsureSigned<AccountId32>>` as configured [9](#0-8) .
2. Attacker creates asset `X` via `pallet_assets::create`, sets `decimals = 6`.
3. Governance calls `pallet_hyper_fungible_token::register_token` for `X` with `precision = { EVM chain: 18 }`, passing the `ErcDecimalsBelowLocal` check (`18 >= 6`) [10](#0-9) .
4. Attacker calls `send` with `amount = 1_000_000` (1.0 `X` at 6 decimals); tokens are burned and `erc20_amount = 10^18` is dispatched [11](#0-10) .
5. Before the request resolves, attacker calls `pallet_assets::set_metadata` on `X`, changing `decimals` to `0`.
6. Force/await a timeout; `on_timeout` re-reads `local_decimals = 0` live and computes `refund = 10^18 / 10^18 = 1` unit instead of the original `1_000_000` [12](#0-11)  — demonstrating the drift (in the opposite decimals direction the same live-read pattern produces over-minting instead).

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L258-295)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L336-355)
```rust
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
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L398-406)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L75-91)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L239-265)
```rust
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
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L350-373)
```rust
impl pallet_assets::Config for Runtime {
	type RuntimeEvent = RuntimeEvent;
	type Balance = Balance;
	type AssetId = H256;
	type AssetIdParameter = H256;
	type Currency = Balances;
	type CreateOrigin = AsEnsureOriginWithArg<frame_system::EnsureSigned<AccountId32>>;
	type ForceOrigin = EnsureRoot<AccountId32>;
	type AssetDeposit = AssetDeposit;
	type AssetAccountDeposit = AssetAccountDeposit;
	type MetadataDepositBase = MetadataDepositBase;
	type MetadataDepositPerByte = MetadataDepositPerByte;
	type ApprovalDeposit = ApprovalDeposit;
	type StringLimit = ConstU32<50>;
	type Freezer = ();
	type WeightInfo = weights::pallet_assets::WeightInfo<Runtime>;
	type CallbackHandle = ();
	type Extra = ();
	type RemoveItemsLimit = ConstU32<5>;
	type Holder = ();
	type ReserveData = ();
	#[cfg(feature = "runtime-benchmarks")]
	type BenchmarkHelper = XcmBenchmarkHelper;
}
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-59)
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

/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```
