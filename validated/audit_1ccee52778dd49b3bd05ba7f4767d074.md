### Title
Decimal-conversion invariant for cross-chain transfers can go stale after registration, allowing amplified/deflated ERC20 minting - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`, `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`pallet-hyper-fungible-token` enforces `erc_decimals >= local_decimals` only at `register_token`/`update_token` time, using a **live snapshot** of the local asset's `decimals()` metadata. The actual cross-chain amount scaling in `send()`, `on_accept`, and `on_timeout` re-queries `decimals()` **live, on every call**, and the scaling helpers `convert_to_erc20`/`convert_to_balance` use `saturating_sub` on the decimal difference. If the local asset's `decimals()` metadata changes after registration (mutable, non-bridge-privileged metadata on `pallet-assets`), the invariant silently breaks and the conversion collapses to a no-op multiplier, producing wildly wrong minted amounts on the destination chain for the same escrowed input — the same class of bug as the reported "protocol assumes 18 decimals" issue, but here it's "protocol assumes decimals never change after registration."

### Finding Description
`register_token` and `update_token` gate the decimal relationship with: [1](#0-0) 

This check reads `local_decimals` live from `<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(...)` at the moment of registration and stores only `config.decimals` (the ERC side) in `Precisions`. The **local** side's decimals are never cached or pinned.

Then in `send()`, decimals is fetched live again, independently, every time a transfer is dispatched: [2](#0-1) 

The scaling itself: [3](#0-2) 

`saturating_sub` means that once `erc_decimals < local_decimals` (the exact condition the registration-time `ensure!` was meant to forbid), the multiplier exponent clamps to `0`, i.e. `convert_to_erc20` becomes a straight pass-through of the raw balance value — no compensation is applied for the decimal mismatch. The same pattern is mirrored in reverse for `convert_to_balance` used in `on_accept`/`on_timeout`.

`pallet-assets` metadata `decimals` is a display-only value that can be updated by the asset's owner/issuer independent of the pallet's own balance accounting (`set_metadata` mutates the `Metadata` storage item, it does not rescale any account balances). In the `gargantua` runtime, asset creation itself is permissionless: [4](#0-3) 

So an ordinary user can create an asset, get it registered by governance for bridging with `config.decimals` fixed at some value (e.g. 6), and later change that asset's own `decimals` metadata (a normal, permissionless-owner action on their own asset) to a higher value (e.g. 18). From that point on, every `send()` call for that asset silently drops the compensating divide-down that should apply for the ERC20 side having fewer decimals than the local side, so the amount minted on the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract is computed as if no scaling were needed, producing a mismatch between the value escrowed on the source chain and the value minted on the destination chain.

### Impact Explanation
This breaks the core Hyperbridge invariant that bridged value must move exactly once and in the exact corresponding amount between chains. A decimals mismatch introduced after registration causes the bridge to mint an incorrect (potentially far larger) amount of the destination ERC20 token relative to what was actually escrowed on the source chain — i.e. unauthorized creation of destination-side value / fund loss for the pallet's escrow, matching the bounty's "stealing or loss of funds" and "logic attacks" categories. The reverse direction (`on_accept`/`on_timeout`, which also depends on live `decimals()`) is symmetrically affected and can under- or over-credit beneficiaries or refunds.

### Likelihood Explanation
The vulnerable code path (`send`, `on_accept`, `on_timeout`, `convert_to_erc20`, `convert_to_balance`) is fully local and verifiable in this repo. The precondition — that an asset's metadata `decimals` can be mutated by its owner independently of the bridge's `register_token`/`update_token` governance calls, and that `pallet-hyper-fungible-token` re-reads `decimals()` live rather than storing a fixed value at registration — is directly evidenced by the code shown above. What I could **not** verify from local, vendored source (pallet-assets is an external `polkadot-sdk` dependency not vendored in this repo) is the exact authorization rule for `pallet_assets::set_metadata` (i.e., whether it is gated by `Issuer` role, and whether the permissionless creator of an asset retains that role by default). This is standard, well-documented Substrate `pallet-assets` behavior (creator receives owner/issuer/admin/freezer roles unless explicitly reassigned), but it is an assumption about external-crate semantics rather than something directly readable in this repo's code, so it should be confirmed against the exact `pallet-assets` version pinned here before treating this as fully proven.

### Recommendation
- Snapshot and store the local asset's decimals in `Precisions` (or a dedicated storage item) at `register_token` time, and use that stored value in `send`/`on_accept`/`on_timeout` instead of re-querying `Inspect::decimals()` live.
- Re-validate `erc_decimals >= local_decimals` on every dispatch/accept path, not only at registration/update, and reject the transfer with `ErcDecimalsBelowLocal` if the invariant no longer holds.
- Replace `saturating_sub` in `convert_to_erc20`/`convert_to_balance` with a checked subtraction that hard-errors if the exponent would be negative, rather than silently collapsing to a no-op scale.

### Proof of Concept
1. On the `gargantua` runtime, an attacker (unprivileged, signed account) calls `pallet_assets::create` to create `AssetX` (permissionless `CreateOrigin`), then `set_metadata(AssetX, decimals=6)`.
2. Bridge governance calls `hyper_fungible_token::register_token` for `AssetX` with `config.decimals = 6` for some EVM chain — passes `ensure!(config.decimals >= local_decimals)` since `6 >= 6`.
3. Attacker calls `pallet_assets::set_metadata(AssetX, decimals=18)` on their own asset (no bridge pallet interaction required, purely a `pallet-assets` call the attacker is authorized for as the asset's issuer/owner).
4. Attacker calls `hyper_fungible_token::send` for `AssetX` with amount `X` (small, e.g. `1`).
   - `decimals` is read live as `18` [5](#0-4) .
   - `erc_decimals` is `6` (from step 2).
   - `convert_to_erc20(X, 6, 18)` computes exponent `6u8.saturating_sub(18) = 0` [6](#0-5) , so `erc20_amount = X` unscaled, instead of the correctly divided-down value the invariant was designed to guarantee.
5. The destination `HyperFungibleToken` contract mints `X` raw units of a token that should have received `X / 10^12` units, resulting in a 10^12x amplification of minted value relative to escrowed value for that asset.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L267-295)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L54-58)
```rust
/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L350-365)
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
```
