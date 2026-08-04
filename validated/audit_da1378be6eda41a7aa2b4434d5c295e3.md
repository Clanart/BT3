### Title
Stale `ContractToAsset` trust binding left uncleaned on token re-registration allows spoofed asset crediting - ([File: modules/pallets/hyper-fungible-token/src/lib.rs])

### Summary
`register_token` in `pallet-hyper-fungible-token` inserts a new `(chain, token_contract) → local_asset_id` reverse-authentication mapping without checking whether that contract slot is already bound to (or was previously bound to) a different value, and without cleaning up any prior `ContractToAsset` entry for the same `(chain, local_id)` pair when a token is re-registered. This mirrors the `addLender` bug class in the referenced report: the function that wires a new trust relationship into the system (`register_token` binding a remote contract address as the authoritative source for a local asset) performs no equality/consistency check against the pre-existing state it is replacing, leaving a stale, still-trusted binding in storage.

### Finding Description
`ContractToAsset::<T>` is the sole authentication mechanism used by `on_accept` to decide which local asset (and therefore which mint/transfer logic and beneficiary crediting) an incoming ISMP `PostRequest` corresponds to: [1](#0-0) 

`register_token` is the extrinsic used to establish this binding. It writes both the forward map (`TokenContracts`) and the reverse map (`ContractToAsset`) for every `(chain, config)` pair supplied by the caller: [2](#0-1) 

Unlike its sibling extrinsic `update_token`, which explicitly looks up and removes any previous `ContractToAsset` entry before inserting the new one when a chain's contract is replaced: [3](#0-2) 

`register_token` performs **no such lookup or cleanup**. It also has no guard against being called a second time for an already-registered `local_id`/chain pair with a different `token_contract`. Consequently, if a token's contract address on a given `StateMachine` is re-pointed via `register_token` (rather than `update_token`), the OLD `(chain, old_contract) → local_id` entry in `ContractToAsset` is never removed — it remains permanently trusted. Since `ContractToAsset` is the *only* authentication check `on_accept` performs (no additional binding to a "current" contract set, no expiry, no cross-check against `TokenContracts`), any future message whose `from` field matches that stale, orphaned contract address will still be accepted and minted/transferred to the attacker-chosen beneficiary as if it were a legitimate transfer of that asset — this is exactly the "no check that the new entry's identity is consistent with what it's replacing" defect called out in the `addLender` report, applied to a bridge custody/authentication mapping instead of a `want` token address.

### Impact Explanation
This breaks the core bridge custody invariant that only the currently-designated, single legitimate remote contract should be able to trigger local minting/unlocking for an asset. A leftover trusted binding is a live authentication backdoor: whoever can get code deployed at the stale contract address on the peer EVM chain (e.g., via `CREATE2`/`SELFDESTRUCT` address reuse, chain redeployment, or simply because the "old" address was mis-registered and never actually corresponded to a real, permanently-controlled contract) can dispatch an ISMP `PostRequest` from that address and have it accepted by `on_accept`, minting the local asset to an arbitrary beneficiary with no real collateral backing it — false state acceptance leading to unbacked minting / fund loss for holders of the affected asset.

### Likelihood Explanation
Exploitation requires the token administrator (`CreateOrigin`) to have re-registered a token's chain contract via `register_token` (instead of the correctly-cleaning `update_token`) — a plausible operational path since both extrinsics are documented as valid ways to configure a token, and nothing in `register_token` warns against or blocks re-registration of the same asset. Once that stale entry exists, the actual message-forging step is available to any unprivileged party able to deploy or resurrect a contract at the old address on the peer chain and route a normal cross-chain message through it — no relayer, prover, or governance compromise is needed to trigger the acceptance path itself.

### Recommendation
In `register_token`, before inserting a new `ContractToAsset`/`TokenContracts` entry for a `(chain, local_id)` pair, look up and remove any existing `TokenContracts`/`ContractToAsset` binding for that pair (mirroring the cleanup already done in `update_token`), and/or reject re-registration of an already-registered `local_id` outright, forcing all contract-address changes through `update_token`'s cleanup path.

### Proof of Concept
1. `CreateOrigin` calls `register_token` for `asset_id = X` with `chains = { Evm(1) => ChainConfig { token_contract: 0xAAA…, decimals: 18 } }`. Storage now has `TokenContracts[(Evm(1), X)] = 0xAAA` and `ContractToAsset[(Evm(1), 0xAAA)] = X`.
2. Later, `CreateOrigin` calls `register_token` again for the same `asset_id = X` with `chains = { Evm(1) => ChainConfig { token_contract: 0xBBB…, decimals: 18 } }` (e.g., migrating to a new EVM contract). `TokenContracts[(Evm(1), X)]` is overwritten to `0xBBB`, and `ContractToAsset[(Evm(1), 0xBBB)] = X` is added — but `ContractToAsset[(Evm(1), 0xAAA)] = X` is **never removed** (contrast with `update_token`'s explicit removal at [4](#0-3) ).
3. Anyone able to get a contract deployed at `0xAAA` on `Evm(1)` (e.g., via `CREATE2`/self-destruct redeploy, or if `0xAAA` was simply a decommissioned/compromised contract) can dispatch an ISMP `PostRequest` with `from = 0xAAA`.
4. `on_accept` resolves `ContractToAsset::<T>::get(Evm(1), 0xAAA)` → still returns `X`, passing authentication, and mints/unlocks asset `X` to the attacker-supplied beneficiary per the forged message body.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L54-56)
```rust
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L356-368)
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
