### Title
`update_token` chain reconfiguration strands in-flight escrowed/burned funds by deleting `ContractToAsset` before pending timeouts resolve - ([File: modules/pallets/hyper-fungible-token/src/lib.rs])

### Summary
`pallet-hyper-fungible-token`'s `update_token` extrinsic lets `CreateOrigin` add or remove a chain's contract mapping for a registered token. When a chain's contract address is changed (`add_chains`) or removed (`remove_chains`), the pallet immediately deletes the `ContractToAsset` reverse-lookup entry keyed on the *old* contract bytes. Any `send()` transfer that was dispatched to that old contract before the update, and that has not yet been delivered or timed out, becomes unrefundable: `on_timeout` looks up `ContractToAsset::<T>::get(dest, &to)` using the exact `to` bytes embedded in the original request, finds nothing, and errors out. The escrowed/burned value from that `send()` is then permanently unaccounted for — funds locked in the pallet's escrow account (or burned outright) with no code path left to release them. This mirrors the reported Renzo pattern of an admin safely reconfiguring collateral/chain support while pending obligations still reference the old configuration, breaking the invariant that "every outstanding obligation must remain resolvable."

### Finding Description
Storage relevant to this pallet:
- `TokenContracts<StateMachine, AssetId> -> Vec<u8>` — used as the `to` address when dispatching outgoing `send()` requests. [1](#0-0) 
- `ContractToAsset<StateMachine, Vec<u8>> -> AssetId` — reverse lookup used by both `on_accept` and `on_timeout` to identify which local asset an incoming message or timed-out request refers to. [2](#0-1) 

`update_token` handles `add_chains` and `remove_chains`. For `add_chains`, if a `TokenContracts` entry already exists for that chain, the pallet removes the *old* `ContractToAsset` mapping before inserting the new one: [3](#0-2) 

For `remove_chains`, both `ContractToAsset` and `TokenContracts` entries are deleted outright: [4](#0-3) 

`send()` escrows (native) or burns (non-native) the user's balance and dispatches a POST request whose `to` field is the `TokenContracts` value at the time of sending: [5](#0-4) 

If that request is never delivered and eventually times out, `on_timeout` is the *only* path that returns the user's funds. It re-derives the asset purely from `ContractToAsset::<T>::get(dest, &to)`, where `to` is the same old contract bytes captured in the original request: [6](#0-5) [7](#0-6) 

If `update_token` has already deleted that `(dest, to)` entry — because the chain's contract address was rotated or the chain was removed — this lookup returns `None`, `on_timeout` errors with `UnknownContractOnTimeout`, and the escrow/burn from the original `send()` is never reversed. There is no other extrinsic or path in this pallet that can re-credit the sender once `ContractToAsset` no longer resolves the pair.

This is the direct structural analog of the Renzo finding: a legitimate, non-malicious admin operation (`removeCollateralToken` there, `update_token` here) mutates state that in-flight user obligations still depend on, without any check for outstanding pending requests, permanently breaking the accounting/refund invariant for those obligations.

### Impact Explanation
Escrowed native-asset balances or burned non-native balances tied to any `send()` in flight at the moment of an `update_token` chain/contract change become permanently stranded: the user cannot claim a timeout refund, and there is no admin recovery extrinsic for this specific case. This is a direct loss-of-funds condition matching the bounty's "stealing or loss of funds" and "logic attacks" categories — user balances are burned/locked with no corresponding release, while the destination side (if it happens to still deliver, though unlikely once the contract is rotated) or the timeout path cannot reconcile.

### Likelihood Explanation
This requires no malicious actor and no compromised key: any normal governance/admin call to `update_token` (a routine config-maintenance operation — rotating a token contract address or removing a decommissioned chain) that occurs while there are outstanding, undelivered `send()` requests to the affected `(chain, old_contract)` pair triggers the bug. Given that ISMP request timeouts are typically hours-to-days in duration, and token contract migrations/chain deprecations are a documented, expected pallet operation via `update_token`, the window for overlap with in-flight transfers is realistic and not a contrived edge case.

### Recommendation
Before deleting or overwriting a `ContractToAsset` / `TokenContracts` entry in `update_token`, either:
1. Keep the old `ContractToAsset` entry alive (in a secondary "retired contracts" map) until all in-flight requests referencing it have either been delivered or timed out, so `on_timeout` can still resolve the asset; or
2. Require the caller to prove/declare there are no pending outbound commitments referencing the old `(chain, contract)` pair before allowing the update (an on-chain counter of in-flight sends per `(chain, contract)` that must be zero); or
3. Change `on_timeout`'s asset resolution to not depend solely on the current `ContractToAsset` mapping — e.g., encode the local `asset_id` directly in the outgoing `Message`/request metadata so timeout processing is self-contained and immune to later reconfiguration.

### Proof of Concept
1. `register_token` registers `asset_id = X` (native = true) with `chains = { Evm(1) => contract_A }`.
2. Alice calls `send(asset_id: X, destination: Evm(1), amount: 100, timeout: T)`. The pallet escrows 100 units from Alice into the pallet account and dispatches a POST request with `to = contract_A`. [5](#0-4) 
3. Before the request is delivered or times out, governance calls `update_token` with `add_chains = { Evm(1) => contract_B }` (rotating the contract address) — this deletes `ContractToAsset(Evm(1), contract_A)`. [3](#0-2) 
4. The original request to `contract_A` is never delivered (it's stale/dead since the peer contract moved) and eventually times out. `pallet-ismp` invokes `on_timeout` with the original request whose `to = contract_A`.
5. `on_timeout` executes `ContractToAsset::<T>::get(Evm(1), contract_A)` → `None` → returns `Err(HftError::UnknownContractOnTimeout)`. [6](#0-5) 
6. Alice's 100 escrowed units are never returned; there is no other call path to release them. Funds are permanently stuck in the pallet's custody account, while Alice's local balance was already debited at step 2.

### Citations

**File:** modules/pallets/hyper-fungible-token/README.md (L40-41)
```markdown
| `TokenContracts` | `DoubleMap<StateMachine, AssetId → Vec<u8>>` | EVM contract address of a token on the given chain. Used as the `to` field on outgoing `DispatchPost`. |
| `ContractToAsset` | `DoubleMap<StateMachine, Vec<u8> → AssetId>` | Reverse lookup; on `on_accept` the source contract is mapped back to the local asset. |
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-310)
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L407-420)
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
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L423-430)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/error.rs (L59-60)
```rust
	#[error("Unknown contract on timeout")]
	UnknownContractOnTimeout,
```
