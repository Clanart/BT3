### Title
Shared native-asset escrow pot lacks per-request accounting and is susceptible to ED reaping, causing loss of unrelated pending cross-chain transfers - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`, `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
The pallet escrows native currency for cross-chain `send()` calls into a single, shared custodial account, `Pallet::<T>::pallet_account()`, with no per-request ledger and no mechanism protecting that account from existential-deposit (ED) reaping. [1](#0-0)  Since `on_accept`/`on_timeout` payouts move funds out of this pot using `ExistenceRequirement::AllowDeath`, a payout for one in-flight request can push the pot's free balance below ED, triggering Substrate's dust-removal/account-reaping, and destroying the native balance implicitly "owed" to other still-pending escrowed transfers.

### Finding Description
`send()` locks native currency by transferring it from the caller into the shared `pallet_account()`: [2](#0-1) 

There is no per-message storage entry recording how much of the pot's balance "belongs" to each individual pending cross-chain request — the pot is a single free-balance account whose composition is only implicit (sum of all currently in-flight escrows).

When the corresponding ISMP response/timeout arrives for *any one* of these pending requests, `on_accept` (delivery) or `on_timeout` (refund) moves exactly that request's own escrowed amount out of the pot, again using `AllowDeath`: [3](#0-2) [4](#0-3) 

Because `pallet_account()` is a plain `PalletId`-derived account with no keep-alive/provider protection or reserved minimum balance, Substrate's `pallet-balances` will destroy (dust-remove) any residual free balance that falls below the chain's Existential Deposit after a transfer. If several native-asset `send()` calls are concurrently in flight (escrowed but not yet settled), and the sum of their escrowed amounts is small enough that completing (accepting or timing out) just one of them leaves the pot's remaining balance below ED, the *entire remaining balance* — which represents other users' still-pending, legitimately escrowed transfers — is wiped out by the runtime's dust-removal logic. This is not a bug in the amount computation for the request being settled (that transfer is exact); it is a structural flaw: the shared pot has no reserved balance and no invariant preventing its free balance from dropping below ED while other requests are still outstanding.

The practical consequence for the victims' unrelated pending requests:
- Their `on_accept`/`on_timeout` transfer, when it eventually fires, will fail with `InsufficientBalance` because the pot's balance was zeroed by dust removal, causing an ISMP callback error and leaving the cross-chain protocol in an inconsistent state where neither the beneficiary is paid nor the original sender is refunded.
- The funds are unrecoverably lost/burned (or sent to whatever the runtime configures for dust collection), constituting silent loss of user funds that were never at fault in the settlement being processed.

### Impact Explanation
This breaks the required invariant that "bridged assets, order escrow, refunds ... must move exactly once and only to the rightful beneficiary and amount." Here, unrelated, correctly-escrowed native balances belonging to other users' pending transfers can be destroyed as a side effect of an unrelated (legitimate) settlement, and the affected users lose funds with no path to recovery, and the corresponding cross-chain leg for their transfer will fail. This matches the bounty's "stealing or loss of funds" / "wrongful asset movement" impact categories.

### Likelihood Explanation
This can be triggered purely by ordinary unprivileged usage of `send()` for the native asset by any set of users, combined with the ordinary relayer flow completing one of several concurrently in-flight small-value native transfers — no malicious relayer, prover, or privileged operator is required. Likelihood scales with how close aggregate in-flight escrow amounts are to the chain's ED, and with the number of concurrent small in-flight native transfers, which is plausible on high-throughput or low-ED chains, or simply when the last pending escrow is smaller than ED.

### Recommendation
- Track escrowed native balances per in-flight request (e.g. a storage map keyed by commitment/request-id) rather than relying on the pot's implicit aggregate balance, or
- Reserve/hold the ED permanently in `pallet_account()` (e.g., fund it at genesis and never let `AllowDeath` transfers reduce it below that floor — use `ExistenceRequirement::KeepAlive` for pot-outgoing transfers so a payout that would kill the account instead fails safely rather than silently destroying other users' escrow), and/or
- Use `frame_system::Pallet::<T>::inc_providers` together with a keep-alive transfer policy so the custodial account is guaranteed to survive as long as any request is outstanding.

### Proof of Concept
1. Configure a chain with `ExistentialDeposit = 10`.
2. User A calls `send()` for the native asset with `amount = 5`, escrowing 5 into `pallet_account()` (lib.rs:260-265). Pot balance = 5 (below ED but tolerated because balance was not yet touched by a `AllowDeath`-reducing transfer, this is the initial deposit — depending on pallet-balances semantics the very first transfer-in could itself get reaped if it can't meet ED, but assume multiple sends bring balance above ED first).
3. User B calls `send()` for the native asset with `amount = 5`. Pot balance = 10 (== ED, alive).
4. The cross-chain response for A's request arrives; `on_accept`/`on_timeout` executes `NativeCurrency::transfer(&pallet_account(), &beneficiary_or_refund, 5, AllowDeath)` (module.rs:94-101 / 257-265). Pot balance drops to 5, which is below ED of 10 → `pallet-balances` reaps the account and dust-removes the remaining 5.
5. When B's request is later settled, the same transfer call fails with `InsufficientBalance` because the pot now holds 0 — B's escrowed 5 units are permanently lost, and B is neither paid on the destination chain nor refunded on timeout. [2](#0-1) [5](#0-4) [6](#0-5) [1](#0-0)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L24-30)
```rust
impl<T: Config> Pallet<T> {
	/// Returns the pallet's custodial account for holding native assets
	pub fn pallet_account() -> T::AccountId {
		use frame_support::PalletId;
		use sp_runtime::traits::AccountIdConversion;
		PalletId(*b"hft__acc").into_account_truncating()
	}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-266)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L90-101)
```rust
		)
		.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;

		// Mint or transfer to beneficiary
		if local_asset_id == T::NativeAssetId::get() {
			<T as Config>::NativeCurrency::transfer(
				&Pallet::<T>::pallet_account(),
				&beneficiary,
				amount,
				ExistenceRequirement::AllowDeath,
			)
			.map_err(|e| HftError::TransferFailed(e.into()))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L256-266)
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
```
