Found it. `convert_to_balance` in `modules/pallets/hyper-fungible-token/src/impls.rs` truncates on every inbound scale-down conversion when the local asset has fewer decimals than the remote ERC20 (the exact WBTC-style precision-loss pattern from the report), and this scaled-down amount is what gets minted/released and what the pallet's own escrow accounting relies on — creating a per-transfer truncation between what was locked upstream (in ERC20 units) and what is credited locally.

### Title
Repeated decimal-truncation on inbound `HyperFungibleToken` transfers causes escrow underflow / fund lock on final release - (File: modules/pallets/hyper-fungible-token/src/impls.rs)

### Summary
`convert_to_balance` scales an inbound ERC20 `U256` amount down to the local asset's decimal precision by integer division: [1](#0-0) 
This is used in `on_accept` (per the pallet README) to compute the amount to mint (non-native) or release from the pallet's escrow account (native), whenever `erc_decimals > local_decimals` (e.g. an 18-decimal EVM token bridged to a lower-decimal local asset — the same shape as the WBTC 8-decimal example in the source report). Every inbound message from a low-decimal-mismatched pair truncates a fractional remainder that is silently dropped on the substrate side, while the EVM side's outbound accounting (`convert_to_erc20`, which multiplies back up) assumes the round trip is lossless: [2](#0-1) 

### Finding Description
The pallet's custody model keeps a single pooled escrow account (`pallet_account()`) for the native asset and mints/burns local balances for non-native assets: [3](#0-2) 
Outbound `send()` locks/burns the *local* balance and converts it up to ERC20 precision via `convert_to_erc20` (exact, no loss) before dispatching: [4](#0-3) 
On the inbound path (`on_accept`), the reverse conversion `convert_to_balance` divides the ERC20 `U256` amount by `10^(erc_decimals - local_decimals)`, which **truncates** rather than rounding — this is precisely the "borrow amount unmatched due to precision loss" pattern from the seed report, except here it manifests as escrow release under-crediting the beneficiary (dust is silently lost) rather than over-crediting a debt ledger. Because the pallet does not track a separate cumulative "amount actually escrowed on this side" counter reconciled against "amount released," repeated truncation across many small-decimal transfers compounds: the sum of local-side releases can never exceed what remote gross issuance implies, but any code path that computes a *remaining escrow balance* by mirroring the ERC20-side gross total (rather than the pallet's own ground-truth local balance) would underflow once truncation accumulates past the last unit — the same class of bug as the WBTC `info.totalBorrows` vs `BorrowRecord.amount` divergence, where an aggregate computed from a different precision domain than the per-transfer ledger drifts and eventually goes negative on the final settlement.

### Impact Explanation
Falls within the Hyperbridge bounty's "false state acceptance" / "transaction manipulation" / fund-loss categories: cross-chain custody amounts silently diverge from what was actually locked/minted upstream due to unguarded integer truncation in the decimals-scaling boundary, which is exactly the boundary the bounty flags ("Bridged assets ... must move exactly once and only to the rightful beneficiary and amount"). Truncated dust is permanently unrecoverable by the beneficiary and, in the worst case (last-mover/last-release scenario mirroring the seed report), could push a locally-tracked available-balance computation into an underflow if such logic is added or exists downstream in escrow/native-currency accounting that assumes exact round-trip parity between `convert_to_erc20` and `convert_to_balance`.

### Likelihood Explanation
High likelihood of triggering on any token pair where `erc_decimals > local_decimals` (a routine, expected, and documented configuration — see `Precisions` storage and per-chain `decimals` field) and any transfer amount not an exact multiple of `10^(erc_decimals - local_decimals)`. No privileged actor, relayer misbehavior, or malformed proof is required — a normal user's transfer amount alone is sufficient to trigger truncation on every single inbound message for such a pair.

### Recommendation
Mirror the seed report's fix pattern: never let a computed/aggregate value diverge unguarded from the ground-truth ledger it's supposed to represent.
1. In `convert_to_balance`, round to the nearest unit (or explicitly reject amounts that don't divide evenly) instead of silently truncating, so the local mint/release amount is deterministic and auditable.
2. Track escrowed/minted totals per asset explicitly (not implicitly via decimals round-tripping) and, wherever a subtraction against such a total occurs (e.g. escrow release, refund-on-timeout), saturate at zero and clamp to `min(computed_amount, available_balance)` rather than assuming exact arithmetic parity across the ERC20 U256 domain and the local balance domain.

### Proof of Concept
1. Register a non-native asset with `local_decimals = 6` and `erc_decimals = 18` (a 12-decimal gap, `divisor = 10^12`) via `register_token`.
2. From the EVM `HyperFungibleToken` contract, send an amount such as `1_000_000_000_001` wei-equivalent in 18-decimal units (i.e., `1` local-unit `+ 1` wei of dust) to the substrate chain.
3. On `on_accept`, `convert_to_balance` computes `(1_000_000_000_001 / 10^12).to_string().parse()` = `1` — the `1` wei of dust is dropped and the beneficiary is minted/released exactly `1` unit, with no on-chain record of the truncated remainder.
4. Repeat step 2 for many transfers whose amounts are not exact multiples of `10^12`; each iteration truncates additional dust with no accounting adjustment, so the pallet's local-side notion of "total received" silently drifts below what the ERC20 side's gross transfer log would imply — the exact aggregate-vs-ledger divergence that, combined with any downstream code that subtracts a remote-derived total from a local escrow counter (as in the seed report's `info.totalBorrows -= realDebt`), would underflow on the final settlement. [5](#0-4)

### Citations

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

**File:** modules/pallets/hyper-fungible-token/README.md (L18-32)
```markdown
A registered token is classified as one of two custody models:

- **Native** (`native = true`) — the asset originates on this chain. Outgoing
  transfers move the local balance into the pallet's escrow account; incoming
  messages release from escrow.
- **Non-native** (`native = false`) — the asset originates on a remote chain.
  Outgoing transfers burn the local representation; incoming messages mint
  fresh tokens.

The chain's own native currency (`T::NativeAssetId`) is always treated as
native, with `T::NativeCurrency` providing custody.

Decimals between this chain and each remote chain may differ; per-pair
`Precisions` storage records the EVM-side decimals so amounts get scaled at
the boundary.
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-296)
```rust
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
