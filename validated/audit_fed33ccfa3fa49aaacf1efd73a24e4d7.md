## Analog Identified: Missing Uniqueness/Existing-Registration Checks in `pallet-hyper-fungible-token::register_token`

The external report (M-2) is about `addPlugin()` accepting a new plugin address with no check that it's (a) a valid/correctly-bound contract, or (b) not a duplicate of an already-registered plugin — leading to double counting and malfunction. The direct Hyperbridge analog is in `register_token`/`update_token` of `pallet-hyper-fungible-token`, which write into two coupled maps (`TokenContracts` and its reverse index `ContractToAsset`) with no cross-asset uniqueness check.

### Title
Missing address-uniqueness check in `register_token` lets a token-contract address be bound to two different asset IDs, silently hijacking `ContractToAsset` routing - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`register_token` inserts into `TokenContracts` and `ContractToAsset` without checking whether the supplied `token_contract` bytes are already bound to a *different* `AssetId` on that chain, and without checking whether `registration.local_id` is already registered at all. `update_token` at least clears the *old* mapping for the *same* asset before inserting a new one, but `register_token` performs no such cleanup or collision check for a *new* registration.

### Finding Description [1](#0-0) 

`register_token` loops over `registration.chains` and unconditionally does:
```
TokenContracts::<T>::insert(chain, registration.local_id.clone(), token_contract.clone());
ContractToAsset::<T>::insert(chain, token_contract, registration.local_id.clone());
```
There is no check that:
1. `token_contract` is not already the reverse-mapped key for a *different* `AssetId` in `ContractToAsset` on the same `chain` (a "duplicated address" scenario, directly matching M-2's missing "ensure `_plugin` is new" check).
2. `registration.local_id` is not already registered (re-registration silently overwrites an existing asset's entire per-chain contract set, with no removal of that asset's old `ContractToAsset` entries, leaving stale reverse-lookup rows).

Compare with `update_token`, which does perform partial cleanup for the *same* asset: [2](#0-1) 
even `update_token` never checks whether the *new* `token_contract` collides with another asset's existing `ContractToAsset` entry — it only clears the old entry belonging to its own `asset_id`.

The `on_accept` inbound path relies entirely on `ContractToAsset` to determine which local asset a message belongs to (per the pallet README): [3](#0-2) 
`ContractToAsset` is the sole authority mapping "message came from this EVM address" → "credit this local asset." If two assets end up bound to the same contract address (via a second `register_token` call, an operational mistake, or a permissive `CreateOrigin` as documented for the sibling deprecated `token-gateway` pallet, which explicitly wires `CreateOrigin = frame_system::EnsureSigned<AccountId>`): [4](#0-3) 
then any legitimate inbound message from that EVM contract is credited to whichever `AssetId` last won the `ContractToAsset::insert` race, while `TokenContracts` for the first asset still silently points outbound traffic at the same (now-shared) address, so this asset's `send()` continues dispatching to a contract that this pallet's own state has re-attributed to another asset.

### Impact Explanation
This directly reproduces the M-2 impact pattern: a contract-address collision causes **misattribution of value between assets** rather than an outright revert. Because `on_accept` mints/releases based purely on the `ContractToAsset` lookup, an inbound transfer intended for asset A gets minted/released as asset B (wrong beneficiary asset / wrong amount class), and asset A's outbound `send()` path keeps routing to a contract whose Hyperbridge-side identity has been reassigned — a fund-misdirection condition with no built-in recovery, matching the bounty categories "unauthorized transaction or execution" / "transaction manipulation" / "wrong beneficiary or amount".

### Likelihood Explanation
`register_token`/`update_token` are gated by `T::CreateOrigin`, but this trait is deployment-configurable, and the sibling deprecated `pallet-token-gateway` documentation explicitly demonstrates configuring the equivalent origin as `EnsureSigned<AccountId>` (any signed account). Even under a stricter `CreateOrigin`, the pallet itself provides **no defense-in-depth**: two honest governance calls (e.g., registering asset B after mistakenly reusing asset A's EVM contract address, or a race between two pending proposals) are enough to trigger the collision — there is no `ensure!` anywhere in `register_token` comparable to Sherlock M-2's recommended checks.

### Recommendation
In `register_token` (and the add path of `update_token`), before inserting into `ContractToAsset`, check:
```rust
ensure!(
    ContractToAsset::<T>::get(chain, &token_contract).is_none()
        || ContractToAsset::<T>::get(chain, &token_contract) == Some(registration.local_id.clone()),
    Error::<T>::ContractAlreadyRegistered
);
```
and additionally guard against silent re-registration of an already-registered `local_id` in `register_token` (require callers to use `update_token` for existing assets), mirroring M-2's two recommendations: validate the target isn't already bound elsewhere, and validate this is genuinely a new registration.

### Proof of Concept
1. Governance (via `CreateOrigin`) calls `register_token` for `asset_id = A` with `chains = { Evm(1) => contract 0xABC }`. `TokenContracts[Evm(1)][A] = 0xABC`, `ContractToAsset[Evm(1)][0xABC] = A`.
2. Governance later calls `register_token` for `asset_id = B` with `chains = { Evm(1) => contract 0xABC }` (same address, by mistake or via a second, differently-scoped `CreateOrigin` proposal). No check fires; `ContractToAsset[Evm(1)][0xABC]` is overwritten to `B`. `TokenContracts[Evm(1)][A]` still equals `0xABC`.
3. A user calls `send` for asset A to `Evm(1)`; the pallet dispatches to `0xABC` as before.
4. Any inbound message later received from `0xABC` is now resolved via `ContractToAsset` to asset `B`'s `on_accept` path, crediting/minting `B` instead of `A` — reproducing the M-2 "double counting / wrong beneficiary" outcome from an un-vetted duplicate address.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L344-368)
```rust
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
				let token_contract = config.token_contract.0.to_vec();
				TokenContracts::<T>::insert(
					chain,
					registration.local_id.clone(),
					token_contract.clone(),
				);
				ContractToAsset::<T>::insert(
					chain,
					token_contract,
					registration.local_id.clone(),
				);
				Precisions::<T>::insert(registration.local_id.clone(), chain, config.decimals);
			}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L407-419)
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
```

**File:** modules/pallets/hyper-fungible-token/README.md (L81-87)
```markdown
## ISMP module behaviour

- `on_accept` — receives `Send` messages from the paired EVM contract. Maps
  the source contract back to a local asset via `ContractToAsset`, scales the
  amount using `Precisions`, then mints (non-native) or releases from escrow
  (native) to the beneficiary. Emits `TokenReceived`.
- `on_timeout` — refunds the original sender's balance from escrow or by
```

**File:** docs/content/developers/polkadot/token-gateway.mdx (L81-97)
```text
impl pallet_token_gateway::Config for Runtime {
    // Configured as Pallet ISMP
    type Dispatcher = Ismp;
    // Configured as Pallet Assets
    type Assets = Assets;
    // Configured as Pallet Balances
    type NativeCurrency = Balances;
    // The native asset id — must be the same type as your Assets pallet's AssetId
    type NativeAssetId = NativeAssetId;
    // Origin allowed to create and update assets
    type CreateOrigin = frame_system::EnsureSigned<AccountId>;
    // The precision of the native asset
    type Decimals = Decimals;
    // AssetAdmin account
    type AssetAdmin = AssetAdmin;
    // EVM to substrate account conversion
    type EvmToSubstrate = ();
```
