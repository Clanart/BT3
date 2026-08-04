Confirmed: `send` derives `to = token_contract` from `TokenContracts::get(destination, asset_id)` **at send time** and dispatches with that fixed `to`/`dest` pair, escrowing/burning the user's funds up front. `on_timeout` later re-derives the asset purely by looking up `ContractToAsset::get(dest, to)` using that same original `to` bytes. If `update_token` runs in between and repoints or removes that chain (`remove_chains`, or `add_chains` re-pointing to a new contract), it deletes the old `ContractToAsset` entry with no regard for requests already in flight, permanently breaking the refund lookup for any `send` dispatched before the update.

### Title
Chain reconfiguration in `update_token` permanently strands escrowed/burned funds for in-flight transfers - (File: modules/pallets/hyper-fungible-token/src/lib.rs)

### Summary
`pallet-hyper-fungible-token`'s `send` extrinsic locks (native) or burns (non-native) a user's asset immediately, then dispatches an ISMP `PostRequest` whose `to` field is the token contract address resolved from `TokenContracts` at send time. If that request never gets accepted and instead times out, `on_timeout` recovers which local asset to refund purely by reverse-looking up `ContractToAsset::get(dest, to)`. `update_token`'s `remove_chains` path (and the re-pointing branch of `add_chains`) deletes the old `ContractToAsset` entry unconditionally, with no check for outstanding, undelivered `send` requests still using that mapping.

### Finding Description
- `send` (`modules/pallets/hyper-fungible-token/src/lib.rs:241-325`) escrows/burns funds and builds `DispatchPost{ dest: params.destination, to: token_contract, ... }` where `token_contract = TokenContracts::get(destination, asset_id)` at that moment [1](#0-0) .
- `on_timeout` (`modules/pallets/hyper-fungible-token/src/module.rs:218-296`) is the *only* refund path for an undelivered request, and it resolves the local asset id strictly via `ContractToAsset::<T>::get(dest, &to)`, erroring with `UnknownContractOnTimeout` if that mapping is absent [2](#0-1) .
- `update_token` (`modules/pallets/hyper-fungible-token/src/lib.rs:384-433`) both for `add_chains` (re-pointing) and `remove_chains` unconditionally deletes the existing `ContractToAsset` entry for that `(chain, asset)` pair — `ContractToAsset::<T>::remove(chain, old_contract)` — without checking whether any dispatched-but-not-yet-timed-out `send` still references `old_contract` as its `to` [3](#0-2) .

This is structurally identical to the external report's core flaw: a governance/maintenance action that legitimately mutates an accepted-item registry (`acceptedTokens` in TokenManager / `ContractToAsset` here) is consumed later by a critical accounting path (`liquidate`/`removeAsset` there, `on_timeout` here) that assumes the entry still exists, and its absence silently breaks the invariant rather than failing safely for the affected in-flight operation.

### Impact Explanation
Any user who calls `send` targeting a chain/asset pair, whose configuration is later touched by a routine `update_token` (chain migration, decimal fix, contract redeploy, or full removal) before the message is either accepted on the destination or times out, loses the ability to be refunded: `on_timeout` reverts with `UnknownContractOnTimeout`, so the escrowed native tokens or burned non-native tokens can never be released back to the sender. This is a direct, permanent loss of user funds with no recovery path in the pallet — it fits "stealing or loss of funds" and "bridged assets ... must move exactly once and only to the rightful beneficiary" from the bounty scope. Non-native assets are unrecoverable entirely (burned with no counterpart credit anywhere); native assets remain locked forever in the pallet's escrow account.

### Likelihood Explanation
No malicious peer, relayer, prover, or governance attacker is required. `CreateOrigin`-gated maintenance (adding a new EVM deployment address, correcting decimals, or deprecating a chain) is an expected, routine operational event for a live bridge pallet, and cross-chain message timeouts routinely take significant time (finalization + timeout window on both chains) — a realistic window during which an ordinary configuration update can race with in-flight `send` requests. The bug requires no cooperation from the attacker or the governance actor; an ordinary user's `send` combined with a normal `update_token` sequencing is sufficient.

### Recommendation
- Do not let `update_token`/`register_token` delete `ContractToAsset` entries that may still be referenced by unresolved requests; instead, version the mapping (keep old `(chain, contract) → asset_id` entries retrievable for a grace period long enough to cover the maximum outstanding timeout) or track outstanding commitments and block config changes for a chain until they drain.
- Alternatively, decouple the timeout refund's asset resolution from mutable configuration state entirely: store the resolved `asset_id` (and decimals used) directly in a per-commitment record at `send` time, so `on_timeout`/`on_accept` never need to re-derive it from a registry that can be edited afterward.
- Add explicit tests mirroring the described race: `send` → `update_token` removing that chain → simulate timeout → assert refund still succeeds (currently it reverts).

### Proof of Concept
1. Governance registers asset `X` with chain `Evm(42)` → `contract_A`, populating `TokenContracts(Evm(42), X) = contract_A` and `ContractToAsset(Evm(42), contract_A) = X`.
2. Alice calls `send({ asset_id: X, destination: Evm(42), amount: 100, timeout: T, ... })`. Her 100 units of `X` are burned/escrowed; the pallet dispatches `DispatchPost{ dest: Evm(42), to: contract_A, ... }`.
3. Before the request is delivered/accepted and before it times out, governance calls `update_token` with `remove_chains: [Evm(42)]` (a routine deprecation/migration), which executes `ContractToAsset::remove(Evm(42), contract_A)` and `TokenContracts::remove(Evm(42), X)` per `modules/pallets/hyper-fungible-token/src/lib.rs:423-430`.
4. The request is never accepted on the destination (or the destination pallet no longer trusts the sender). At time `T`, `pallet-ismp` invokes `on_timeout` with `Request::Post{ to: contract_A, dest: Evm(42), .. }`.
5. `on_timeout` executes `ContractToAsset::<T>::get(Evm(42), contract_A)` → `None` → returns `Err(HftError::UnknownContractOnTimeout)` (`modules/pallets/hyper-fungible-token/src/module.rs:236-238`).
6. Alice's 100 units of `X` are never refunded and remain permanently burned/locked; there is no other extrinsic in the pallet capable of crediting her back.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-309)
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
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L407-430)
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L236-238)
```rust
				let local_asset_id = ContractToAsset::<T>::get(dest, &to)
					.ok_or(HftError::UnknownContractOnTimeout)?;

```
