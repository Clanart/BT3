This confirms the mechanism: if `on_timeout` returns an `Err`, `modules/ismp/core/src/handlers/timeout.rs` restores the request commitment (`host.store_request_commitment`) rather than deleting it — the request stays permanently in "pending" state and no refund path is ever executed successfully. Combined with `update_token`'s unconditional `ContractToAsset::remove`, this produces a permanent fund-lock analog to the `clearTokenConfig` report.

### Title
Token re-pointing/removal in `update_token` permanently locks funds for in-flight transfers by breaking the `on_timeout` refund lookup - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`pallet_hyper_fungible_token::update_token` (privileged `CreateOrigin` call) can re-point or remove a chain's `TokenContracts`/`ContractToAsset`/`Precisions` entries with no check for outstanding, in-flight `send` requests still awaiting `on_accept`/`on_timeout` against the *old* contract address on that chain. This mirrors the reported `clearTokenConfig` bug class: configuration is cleared without verifying the token isn't "in use" in active operations, and the refund path becomes unable to complete, leaving user funds locked.

### Finding Description
`send()` escrows/burns the sender's tokens and dispatches a `DispatchPost` whose `to` field is the current `TokenContracts::<T>::get(dest, asset_id)` value [1](#0-0) . If that request later times out, `on_timeout` re-derives the asset purely from `ContractToAsset::<T>::get(dest, &to)`, where `to` is the *original* contract address baked into the already-dispatched `PostRequest` [2](#0-1) , and then reads `Precisions::<T>::get(local_asset_id, dest)` to scale the refund amount [3](#0-2) .

`update_token` lets `CreateOrigin` re-point a chain to a new contract or drop it entirely. Both the "add" and "remove" branches unconditionally erase the *old* contract's `ContractToAsset` mapping and the chain's `Precisions` entry, with no check for outstanding requests still targeting the old contract: [4](#0-3) 

If a user's `send` was dispatched to the old contract address before this reconfiguration, and the request later times out, `on_timeout` executes `ContractToAsset::<T>::get(dest, &to).ok_or(HftError::UnknownContractOnTimeout)?` — this now returns `None` because the mapping for the old contract was removed, so the callback returns `Err` instead of refunding.

Critically, `modules/ismp/core/src/handlers/timeout.rs` treats a failing module callback as "not yet resolved": it restores the request commitment (`host.store_request_commitment`) rather than deleting it, specifically so the timeout can be retried later [5](#0-4) . But retrying doesn't help here — `on_timeout`'s lookup will *always* fail as long as the old contract mapping stays removed, which after `update_token` is permanent (there's no way to re-insert a stale `ContractToAsset` entry pointing at a superseded contract without corrupting the current one). The escrowed/burned funds behind that `send` become permanently unrecoverable: they can never be refunded via `on_timeout`, and were already debited from the sender in `send()`.

### Impact Explanation
This is a direct, protocol-level fund-lock: a user's escrowed native asset (or burned non-native asset) becomes permanently stuck with no code path to recover it, once the admin performs an entirely ordinary and expected operation — reconfiguring a token's chain (e.g., contract migration/upgrade) — while a transfer to the old contract is still in flight. This matches the "locked funds... resulting from removing a configuration without checking for active/pending operations" impact class described in the source report, mapped onto Hyperbridge's request/timeout settlement path (`Request, response, and timeout paths must bind... one-time receipt handling`).

### Likelihood Explanation
`update_token`/`register_token` are exactly the kind of routine, non-malicious admin operations expected in production (contract upgrades, chain additions/removals) as documented in the pallet's own README [6](#0-5) . Any window where a `send()` is dispatched shortly before an unrelated `update_token` re-points/removes that destination chain (a normal race under real network latency and cross-chain finality/challenge periods) triggers the bug — no attacker-controlled peer, relayer, or malicious governance action is required, only the ordinary asynchronous nature of ISMP combined with normal admin maintenance.

### Recommendation
- Before removing/re-pointing a `(chain, asset_id)` config in `update_token`, retain the old `ContractToAsset` (and `Precisions`) entry until all outstanding requests to the old contract have resolved (accepted or timed out), or version the mapping so `on_timeout` can resolve historical contract addresses.
- Alternatively, encode the asset id and decimals directly in the dispatched `Message`/`PostRequest` body at `send()` time rather than re-deriving them from mutable current-state maps at `on_timeout` time, so timeout refunds are self-contained and immune to later reconfiguration.
- Add a grace period / explicit "retired contract" registry that keeps stale reverse mappings alive for refund purposes.

### Proof of Concept
1. `register_token` registers asset `X` with `TokenContracts[Evm(1), X] = contractA`, `ContractToAsset[Evm(1), contractA] = X`, `Precisions[X, Evm(1)] = d`.
2. User calls `send(asset_id=X, destination=Evm(1), amount=A)`; pallet escrows/burns `A` and dispatches `PostRequest{to: contractA, dest: Evm(1), ...}` [7](#0-6) .
3. Before this request resolves, `CreateOrigin` calls `update_token` with `remove_chains = [Evm(1)]}` (or `add_chains` re-pointing `Evm(1)` to `contractB`) — this deletes `ContractToAsset[Evm(1), contractA]` and `Precisions[X, Evm(1)]` [8](#0-7) .
4. The original request from step 2 times out. `pallet-ismp`'s timeout handler calls `on_timeout`, which looks up `ContractToAsset::<T>::get(Evm(1), contractA)` — now `None` — and returns `Err(HftError::UnknownContractOnTimeout)` [2](#0-1) .
5. `modules/ismp/core/src/handlers/timeout.rs` restores the commitment for retry rather than failing permanently [5](#0-4) , but every retry hits the same missing mapping — the user's escrowed/burned `A` is never refunded, permanently locked in the pallet (or destroyed, in the burn case).

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-253)
```rust
			let token_contract =
				TokenContracts::<T>::get(params.destination, params.asset_id.clone())
					.ok_or(Error::<T>::TokenContractNotFound)?;
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L304-315)
```rust
			let dispatch_post = DispatchPost {
				dest: params.destination,
				from: PALLET_ID.to_bytes(),
				to: token_contract,
				timeout: params.timeout,
				body: Message::abi_encode(&token_message),
			};

			let metadata = FeeMetadata { payer: who.clone(), fee: params.relayer_fee.into() };
			let commitment = dispatcher
				.dispatch_request(DispatchRequest::Post(dispatch_post), metadata)
				.map_err(|_| Error::<T>::DispatchError)?;
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L236-237)
```rust
				let local_asset_id = ContractToAsset::<T>::get(dest, &to)
					.ok_or(HftError::UnknownContractOnTimeout)?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L246-247)
```rust
				let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), dest)
					.ok_or(HftError::DecimalsNotConfigured(dest))?;
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L122-134)
```rust
					if res.is_ok() {
						host.on_request_timeout(&request, meta)?;
					} else {
						// Module callback failed; restore commitment so the request
						// can be retried.
						host.store_request_commitment(&request, meta)?;
						if host.host_state_machine() != post.source && signer.is_some() {
							host.store_request_receipt(
								&request,
								&signer.ok_or_else(|| anyhow::anyhow!("Infallible"))?,
							)?;
						}
					}
```

**File:** modules/pallets/hyper-fungible-token/README.md (L49-53)
```markdown
| Call | Origin | Effect |
|------|--------|--------|
| `send(params)` | Signed | Lock or burn the local asset and dispatch a `Send` message to the paired contract on `params.destination`. Emits `TokenSent`. |
| `register_token(registration)` | `CreateOrigin` | Register a new asset, set its custody model (`native`) and per-chain contract+decimals config. Emits `TokenRegistered`. |
| `update_token(update)` | `CreateOrigin` | Add or remove chains from an existing token's configuration. |
```
