## Analysis

**Core broken invariant in the seed report:** a decimals value is fixed once (either hardcoded or checked at config time) and then used forever to scale a monetary amount, but the actual decimals of the underlying asset can diverge from that fixed assumption at the time the scaling is actually applied — with no re-validation and no revert, producing silently wrong amounts.

**Local analog:** `pallet-hyper-fungible-token` enforces the invariant `config.decimals (erc_decimals) >= local_decimals` only once, at `register_token`/`update_token` time. But `local_decimals` is *not* stored — it is re-read live from `pallet-assets` metadata (`Inspect::decimals`) on every `send` and `on_accept`, while `erc_decimals` stays frozen in `Precisions` storage. [1](#0-0) [2](#0-1) [3](#0-2) 

Because pallet-assets' `decimals` metadata is a display field settable post-creation by the asset's `Owner`/admin via `set_metadata` (a normal, non-governance, non-privileged call independent of this pallet's own `CreateOrigin`), an asset owner can change `local_decimals` any time after registration without the bridge pallet re-checking the `config.decimals >= local_decimals` invariant.

### Title
Stale cross-chain decimals invariant lets an asset owner inflate `on_accept` mint amounts - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`, `modules/pallets/hyper-fungible-token/src/module.rs`, `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`register_token`/`update_token` validate `config.decimals (erc_decimals) >= local_decimals` exactly once, using the local asset's decimals *at that moment*. The scaling divisor used in `convert_to_balance`/`convert_to_erc20` is `10^(erc_decimals.saturating_sub(local_decimals))`. `local_decimals` is fetched dynamically from `pallet-assets` on every `send`/`on_accept`, but the frozen `erc_decimals` in `Precisions` storage is never re-validated against it. If the asset's decimals metadata is later raised (a normal, unprivileged `pallet_assets::set_metadata` call by the asset owner) to meet or exceed the registered `erc_decimals`, `saturating_sub` collapses the scaling exponent to `0`, destroying the originally-configured scale factor (which for pairs like `local=12/erc=18` — the pattern the pallet's own benchmarks and docs describe — was `10^6`). The next inbound message then mints the raw incoming amount practically unscaled, over-crediting the beneficiary by the collapsed scale factor.

### Finding Description
- `register_token`/`update_token` check `config.decimals >= local_decimals` only at registration time: [4](#0-3) 
- `Precisions` stores the checked `erc_decimals` permanently, but `local_decimals` is *not* stored — it's re-derived from `pallet-assets` metadata on every call: [5](#0-4) 
- The scaling helper uses `saturating_sub`, silently clamping to `0` (i.e., scale factor `1`) whenever `erc_decimals < local_decimals` at call time — exactly the "won't revert, won't warn, just silently mis-scale" failure mode described in the Tellor report: [6](#0-5) 
- Nothing in `on_accept`/`send` re-checks the original registration invariant; it is trusted forever even though `pallet-assets` decimals metadata is mutable by the asset's own owner outside this pallet's control (documented multi-decimal design intent: [7](#0-6) ).

### Impact Explanation
This is a false-state/wrong-amount acceptance bug that leads directly to unauthorized minting of value on the substrate side: a legitimate but low-value inbound message can, after the decimals drift, mint a beneficiary balance inflated by the originally-configured scale factor (e.g. `10^6` or larger for typical 6–12 vs 18 decimal pairings used across Hyperbridge integrations). This is fund creation from nothing for non-native (mint/burn) assets, and an escrow-draining mismatch for native (lock/release) assets, matching the bounty's "stealing or loss of funds" / "false proof or state acceptance" categories.

### Likelihood Explanation
The only actor needed is the asset's own `pallet-assets` owner — not a bridge admin, relayer, or prover. `set_metadata` is a standard, common, non-privileged Substrate call over which the `hyper-fungible-token` pallet has no oversight after initial registration. Any deployment that follows the pallet's documented pattern of registering an asset with `erc_decimals > local_decimals` (explicitly called out as the intended multi-decimal use case) is exposed once the asset owner changes decimals metadata upward.

### Recommendation
Snapshot `local_decimals` into `Precisions`/registration storage at `register_token`/`update_token` time instead of re-reading live metadata, or re-validate `config.decimals >= local_decimals` (fetched fresh) on every `send`/`on_accept` and reject/revert instead of using `saturating_sub`, which must never silently clamp a decimals mismatch.

### Proof of Concept
1. Asset owner creates asset `X` in `pallet-assets` with `decimals = 6`.
2. Governance calls `register_token` pairing `X` with an EVM contract configured at `erc_decimals = 18` (satisfies `18 >= 6`); `Precisions` stores `erc_decimals = 18`.
3. Attacker (the asset's `pallet-assets` owner) calls `pallet_assets::set_metadata` to raise `X`'s decimals to `18`.
4. Attacker sends any small amount from the paired EVM contract, triggering `on_accept`.
5. `on_accept` reads `local_decimals = 18` live, `erc_decimals = 18` from storage; `convert_to_balance` divisor becomes `10^(18-18)=1` instead of the originally intended `10^(18-6)=10^6`.
6. Beneficiary is minted `10^6`× more local tokens than the bridged value warrants.

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L39-58)
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
```

**File:** modules/pallets/hyper-fungible-token/src/benchmarking.rs (L36-40)
```rust
/// Decimals of the asset the benchmarks bridge. `register_token` rejects an evm side with fewer
/// decimals than the local asset, so this must stay at or below [`EVM_DECIMALS`].
const LOCAL_DECIMALS: u8 = 12;
/// Decimals of the token contract on the evm side.
const EVM_DECIMALS: u8 = 18;
```
