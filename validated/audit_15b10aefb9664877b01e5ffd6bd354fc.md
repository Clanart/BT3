### Title
Updating a token's contract mapping permanently breaks timeout refunds and inbound crediting for in-flight cross-chain transfers - ([File: modules/pallets/hyper-fungible-token/src/lib.rs])

### Summary
`pallet_hyper_fungible_token::update_token` repoints the `(StateMachine, AssetId) → contract` mapping (`TokenContracts`) and its reverse lookup (`ContractToAsset`) whenever a chain's token contract is changed or removed. Both `on_accept` and `on_timeout` authenticate/route incoming ISMP messages solely by looking up `ContractToAsset::<T>::get(source/dest, &from/&to)`. Any request that was dispatched via `send()` *before* the update, but is delivered or times out *after* the update, references the **old** contract address, which no longer exists in `ContractToAsset`. The lookup fails, the callback returns an error, and — per the ISMP timeout/accept semantics — the request commitment is never cleared, meaning the funds that were escrowed or burned at `send()` time can never be credited to the recipient nor refunded to the sender. This is the same "old interface disabled while balances/allowances are still bound to it" failure mode described in the external report, reproduced here through the token-contract migration path.

### Finding Description
`send()` escrows/burns the user's tokens and dispatches a `PostRequest` whose `to` field is the `token_contract` bytes read from `TokenContracts` at that moment: [1](#0-0) 

`update_token` (governance/`CreateOrigin`-gated, but its effects fall on ordinary users' already-in-flight transfers) removes the old `ContractToAsset` entry and installs a new one for the chain/asset pair: [2](#0-1) 

`on_accept` (inbound crediting) and `on_timeout` (outbound refund) both resolve the local asset purely from `ContractToAsset`, keyed by the contract address embedded in the specific request that was already dispatched: [3](#0-2) [4](#0-3) 

Because `update_token` removes the old contract's `ContractToAsset` entry as soon as it processes the chain (`ContractToAsset::<T>::remove(chain, old_contract)`), any request already dispatched to the old contract, but not yet delivered/timed out, permanently loses its ability to resolve `local_asset_id`. `HftError::UnknownSourceContract` / `HftError::UnknownContractOnTimeout` is returned.

The core ISMP timeout handler explicitly does **not** treat a failed `on_timeout` as final — it restores the request commitment so it can be retried: [5](#0-4) 

But since `ContractToAsset` for the old contract is gone forever (nothing re-inserts it), every retry will fail identically — the "will retry" design assumption (transient failure) is violated by a structural, permanent failure. The escrowed/burned funds from the affected `send()` calls are stuck: they can never be refunded via `on_timeout`, and if the peer chain still delivers the message, `on_accept` will reject it too, so the tokens locked/burned on the origin side are never credited on the destination side either.

### Impact Explanation
This is a direct loss/lock of user funds (falls under "stealing or loss of funds"): the `pallet_account()` custody balance (for native assets) or the burned supply (for non-native assets) becomes permanently unrecoverable for any `send()` requests in flight at the time `update_token` runs and reassigns/removes a chain's contract mapping. Ordinary, unprivileged users who called `send()` shortly before a routine token-contract migration (e.g. upgrading the EVM-side ERC20/HyperFungibleToken contract, which is an expected, foreseeable maintenance operation per the pallet's own design) suffer the loss — this mirrors exactly the external report's core scenario: an interface swap silently invalidates the bookkeeping tied to the outgoing/incoming leg, and the corresponding funds vanish from the ledger with no recovery path.

### Likelihood Explanation
This requires no attacker action — it is triggered purely by normal operational use: any legitimate `update_token` call (which the pallet's own `TokenUpdate` type explicitly supports for `add_chains`/`remove_chains`) racing against pending in-flight `send()` transfers or timeouts. Given that Hyperbridge is a cross-chain bridge where messages can be in-flight for arbitrarily long periods before delivery or timeout, and contract migrations are a normal expected event (new token contract deployments, decimal reconfiguration, etc.), the window in which victim funds exist is realistically nonzero and cannot be fully avoided by careful governance timing, since users can call `send()` at any time up to the block the update lands.

### Recommendation
- Do not remove the `ContractToAsset` entry for the old contract immediately; instead retain both the old and new contract → asset mappings for a configurable grace/drain period (or indefinitely, keyed by contract address rather than only by the "current" contract per chain), so that in-flight requests dispatched against the old contract can still be resolved by `on_accept`/`on_timeout`.
- Alternatively, embed the asset id (rather than only the raw contract address) inside the dispatched `Message`/commitment so that `on_timeout`/`on_accept` do not depend on a mutable global mapping that can be changed after dispatch.
- Add an explicit drain/pause mechanism: `update_token`/`remove_chains` should refuse to drop a contract mapping while there exist unresolved outgoing commitments referencing it (or provide an administrative recovery path to permanently reintroduce a removed mapping to unblock stuck timeouts).

### Proof of Concept
1. Governance registers asset `X` for `StateMachine::Evm(1)` with contract `C1` via `register_token`.
2. Alice calls `send()` to transfer `X` to `Evm(1)`; her tokens are escrowed/burned, and a `PostRequest` is dispatched with `to = C1`. `ContractToAsset::<T>::get(Evm(1), C1) == X`.
3. Before the request is delivered or times out, governance calls `update_token` to migrate `X`'s contract on `Evm(1)` to `C2` (a normal contract upgrade). This executes:
   - `ContractToAsset::<T>::remove(Evm(1), C1)`
   - `ContractToAsset::<T>::insert(Evm(1), C2, X)` [6](#0-5) 
4. Case A (timeout): Alice's request later times out. `on_timeout` looks up `ContractToAsset::<T>::get(Evm(1), C1)` → `None` → returns `HftError::UnknownContractOnTimeout`. Per `modules/ismp/core/src/handlers/timeout.rs:122-134`, the commitment is restored for retry, but retrying forever fails the same way — Alice's escrowed/burned tokens are never refunded.
5. Case B (late delivery): if the EVM side still delivers a response/callback referencing `C1` as `from`, `on_accept`'s `ContractToAsset::<T>::get(source, C1)` also fails with `HftError::UnknownSourceContract`, so the destination-side credit never happens either, while the source-side tokens remain locked/burned.

**Uncertainty note:** I was not able to fully trace whether any off-chain relayer/indexer tooling detects and automatically remediates this specific failure mode (e.g., by re-inserting the stale mapping) — the on-chain pallet code itself provides no such recovery path, and only fixing this at the pallet level (per the recommendation) removes the loss condition.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-310)
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
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L398-433)
```rust
			for (chain, config) in update.add_chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
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

			Ok(())
		}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-56)
```rust
	fn on_accept(
		&self,
		PostRequest { body, from, source, .. }: PostRequest,
	) -> Result<Weight, anyhow::Error> {
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L218-237)
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
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L113-134)
```rust
					let res = cb.on_timeout(request.clone()).map(|weight| {
						total_module_weight.saturating_accrue(weight);
						let commitment = hash_request::<H>(&request);
						Event::PostRequestTimeoutHandled(TimeoutHandled {
							commitment,
							source: post.source,
							dest: post.dest,
						})
					});
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
