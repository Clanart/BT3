### Title
`register_token` Never Clears Stale `ContractToAsset` Reverse-Mapping When Re-Registering An Asset's Chain Config - ([File: modules/pallets/hyper-fungible-token/src/lib.rs])

### Summary
In `pallet-hyper-fungible-token`, `register_token` writes `TokenContracts` and `ContractToAsset` for a `(chain, asset_id)` pair without first checking whether an entry already existed. Its sibling extrinsic `update_token` explicitly removes the stale reverse mapping before writing the new one, but `register_token` does not. Calling `register_token` a second time for an asset that is already registered on a given chain (e.g. to point at a redeployed/corrected EVM token contract) leaves the old contract's `ContractToAsset` entry intact, so the decommissioned EVM contract address remains a trusted source of inbound token messages.

### Finding Description
`ContractToAsset` is the reverse lookup used in `on_accept` to map an incoming ISMP message's source contract address back to a local asset for minting/releasing funds: [1](#0-0) 

`register_token` inserts into both `TokenContracts` and `ContractToAsset` unconditionally, with no lookup of a pre-existing `TokenContracts` entry for that `(chain, asset_id)`: [2](#0-1) 

By contrast, `update_token`'s `add_chains` loop explicitly reads the old contract from `TokenContracts` and calls `ContractToAsset::remove` on it before inserting the new mapping: [3](#0-2) 

This is exactly the bug-class from the report: an "add new / replace old" flow updates the forward record (`TokenContracts`) but fails to invalidate the old reverse record (`ContractToAsset`) for the value being replaced, so the old value keeps being treated as valid by other logic (`on_accept`) even though it should no longer be trusted.

If operators use `register_token` (rather than `update_token`) to correct or migrate a token's EVM contract address for a chain it was already registered on — plausible since both extrinsics are gated by the same `CreateOrigin` and `register_token`'s doc/name ("Registers a new token") doesn't warn against re-use for an existing asset — the previous contract address remains permanently valid in `ContractToAsset`. Per the pallet's own documented `on_accept` behavior, any inbound `Send` message whose `from` field matches that old, no-longer-authorized contract address will still be resolved to the local asset and minted/released to the attacker-chosen beneficiary: [4](#0-3) 

### Impact Explanation
This is a false-state-acceptance / wrong-authorization bug for bridge custody: it allows a decommissioned/incorrect EVM contract address to keep minting or releasing the bridged asset on the substrate side indefinitely after it should have been revoked, which can lead to unauthorized minting of a non-native asset or unauthorized draining of native-asset escrow if that old contract is later compromised, redeployed by someone else, or was retired specifically because it was found to be faulty/insecure. This directly matches the bounty's "false proof/state acceptance" and "stealing or loss of funds" categories, since inbound minting/unlocking is gated purely on the stale `ContractToAsset` membership check.

### Likelihood Explanation
Requires only a normal governance/admin operational mistake — re-issuing `register_token` instead of `update_token` when correcting or migrating a chain's token contract for an already-registered asset — which is a realistic operational path given both calls share the same origin and the pallet provides no guard rejecting re-registration of an existing `(chain, asset_id)`. No relayer/prover compromise or malicious governance is needed; the flaw is purely in the missing cleanup logic.

### Recommendation
In `register_token`, before inserting into `TokenContracts`/`ContractToAsset`, check for an existing `TokenContracts::get(chain, asset_id)` entry and, if present, `ContractToAsset::remove` the old contract mapping first — mirroring the logic already implemented in `update_token`'s `add_chains` loop. Alternatively, make `register_token` reject re-registration of an asset/chain pair that already has a `TokenContracts` entry, forcing all subsequent changes through `update_token`, which correctly performs the reverse-mapping cleanup.

### Proof of Concept
1. `CreateOrigin` calls `register_token` with `local_id = ASSET_X`, `chains = { Evm(1): ChainConfig { token_contract: 0xAAA, decimals: 18 } }`. This sets `TokenContracts[Evm(1), ASSET_X] = 0xAAA` and `ContractToAsset[Evm(1), 0xAAA] = ASSET_X`.
2. Later, the operator needs to correct the contract address (e.g., `0xAAA` was misconfigured or is being retired) and calls `register_token` again with the same `local_id = ASSET_X`, `chains = { Evm(1): ChainConfig { token_contract: 0xBBB, decimals: 18 } }`, intending `0xBBB` to be the only valid source going forward.
3. State after step 2: `TokenContracts[Evm(1), ASSET_X] = 0xBBB` (correctly updated), but `ContractToAsset[Evm(1), 0xAAA] = ASSET_X` is still present because `register_token` never called `ContractToAsset::remove(Evm(1), 0xAAA)`.
4. Anyone able to originate (or still controlling) a message from source contract `0xAAA` on chain `Evm(1)` submits a `Send` message through ISMP to this pallet. `on_accept` looks up `ContractToAsset[Evm(1), 0xAAA]`, finds `ASSET_X`, and mints/releases `ASSET_X` to the attacker-specified beneficiary — even though `0xAAA` is no longer the sanctioned contract.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L127-138)
```rust
	/// Reverse lookup: (StateMachine, contract address bytes) → local asset ID.
	/// Used in on_accept to find which local asset an incoming message corresponds to.
	#[pallet::storage]
	pub type ContractToAsset<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		AssetId<T>,
		OptionQuery,
	>;
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L356-367)
```rust
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

**File:** modules/pallets/hyper-fungible-token/README.md (L81-89)
```markdown
## ISMP module behaviour

- `on_accept` — receives `Send` messages from the paired EVM contract. Maps
  the source contract back to a local asset via `ContractToAsset`, scales the
  amount using `Precisions`, then mints (non-native) or releases from escrow
  (native) to the beneficiary. Emits `TokenReceived`.
- `on_timeout` — refunds the original sender's balance from escrow or by
  re-minting. Emits `TokenRefunded`.
- `on_response` — unused; this pallet uses post-only messaging.
```
