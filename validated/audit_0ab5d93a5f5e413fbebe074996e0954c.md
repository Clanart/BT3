## Title
Relayer fee withdrawal pays out `Fees` balances (accrued in one fee-token's units) using whatever `fee_token` is currently configured, after a `fee_token` change — same class as the "changing reward token" bug - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
Hyperbridge's relayer-fee accounting is the direct analog of the `ERC20Rewards.setRewards` bug: the pallet accumulates a relayer's unclaimed fee as a raw `U256` amount (denominated in whatever `feeToken` was configured on the destination chain at the time the ISMP request was dispatched), but pays it out later by re-reading the *current* `fee_token` address from `HostParams` at withdrawal time. If governance changes a chain's `fee_token` (an explicitly supported operation) between accumulation and withdrawal, relayers receive their old numeric balance denominated in the new token, exactly mirroring the reported "different decimals/value token" bug.

### Finding Description
Fee accumulation credits a raw numeric amount to a relayer, decoded from the source-chain's `RequestCommitments`/`RequestMetadata` fee field, with no reference to which token that fee was paid in: [1](#0-0) 

That amount sits in `Fees::<T>` indefinitely until the relayer calls `withdraw_fees`. At withdrawal time, the pallet fetches the *current* `HostParams` for the destination chain and builds the payout message using `params.fee_token` as the token to disburse, and `available_amount` (the raw accumulated balance) as the amount — with no unit/decimals reconciliation against the token that was actually in effect when the fee was accrued: [2](#0-1) 

`HostParams` (including `fee_token`) is fully governance-mutable via `pallet-host-executive::update_host_params`, which merges an `EvmHostParamUpdate` into the stored `EvmHostParam` and dispatches the new params to the destination `HostManager`: [3](#0-2) 

The update struct explicitly supports changing `fee_token`, and the doc-comment on the field is a near-verbatim restatement of the exact hazard from the external report (funds/value mismatch across a token swap), acknowledging the risk without enforcing any invariant against it: [4](#0-3) 

On the EVM side, `HostManager.onAccept` blindly forwards `Withdraw` (`WithdrawParams{token,...}`) and `SetHostParam` (`HostParams`) messages from Hyperbridge to `EvmHost`, so a `fee_token` change and a subsequent withdrawal denominated in the new token both execute through the same authenticated, ordinary flow — no additional gate ties a withdrawal's `token` field back to the token that was active when the underlying fee was collected: [5](#0-4) 

The corrupted value is the `token` field of `WithdrawalParams` built in `withdraw()`: it is populated from the *live* `HostParams::<T>::get(dest_chain).fee_token` rather than the token that was authoritative when `available_amount` was accrued in `accumulate()`, so `available_amount` (raw units of the old token) is disbursed as if it were the same number of units of the new token.

### Impact Explanation
If governance changes a chain's `fee_token` to a token with different decimals or market value (the same scenario the external report describes — e.g. 18-decimal DAI → 6-decimal USDC), every relayer with a pending, unwithdrawn `Fees` balance on that chain will be paid the same raw numeric amount in the new token. This is a direct, protocol-level fund-loss (relayer receives far less than owed) or fund-drain (relayer receives far more than owed, at the expense of the host's fee-token reserves) — squarely within the "stealing or loss of funds" / "wrong beneficiary or amount" impact classes in the bounty gate. Because `Fees` balances can sit unclaimed for arbitrary periods (withdrawal is relayer-initiated, not automatic), any planned or emergency `fee_token` migration is unsafe by construction unless every relayer withdraws first — which the protocol does not enforce.

### Likelihood Explanation
This requires a governance-issued `update_host_params` call changing `fee_token` (a legitimate, documented operation — the code comment itself anticipates it "before changing this parameter, that all funds have been drained"), which is a normal operational action, not a compromised-key or malicious-relayer scenario. Any relayer who simply delays withdrawal (intentionally or not) across such a migration is affected, with no attacker action needed beyond normal fee accrual and a subsequent withdrawal call — this is a passive/systemic bug rather than an active exploit requiring special preconditions.

### Recommendation
- Record the `fee_token` (and/or its decimals) that was authoritative at accumulation time alongside the `Fees` balance (e.g., key `Fees` by `(state_machine, fee_token, account)` or store a token tag in the entry), and pay out using that recorded token rather than the live `HostParams.fee_token`.
- Alternatively, disallow `fee_token` changes while any relayer holds a nonzero `Fees` balance for that chain (mirroring the Yield team's eventual fix of disallowing the token change outright), or require an explicit governance-driven migration step that converts/settles all outstanding `Fees` balances at the old token's value before the new token takes effect.

### Proof of Concept
1. Relayer delivers messages on `StateMachine::Evm(X)` and calls `accumulate_fees`; `Fees::<T>::get(Evm(X), relayer)` is credited, e.g., `1_000e18` (units of the then-current fee token, 18 decimals). [6](#0-5) 
2. Governance calls `pallet_host_executive::update_host_params(Evm(X), EvmHostParamUpdate { fee_token: Some(new_usdc_addr), .. })`, which dispatches `SetHostParam` to `HostManager` on chain X, updating `EvmHost.feeToken` to a 6-decimal token. [7](#0-6) [8](#0-7) 
3. Relayer calls `withdraw_fees` for `dest_chain = Evm(X)`; `withdraw()` reads the now-updated `HostParams` and builds `WithdrawalParams{ amount: 1_000e18, token: new_usdc_addr, .. }`. [2](#0-1) 
4. `EvmHost.withdraw` on chain X (invoked via `HostManager.onAccept`) transfers `1_000e18` raw units of the 6-decimal token to the relayer — a face value roughly 1e12x larger than the original accrued fee, drained from the host's reserves (or, in the opposite decimals direction, the relayer is shortchanged by the same factor).

**Uncertainty**: I could not fully read `EvmHost.sol`'s `withdraw()`/`updateHostParams()` bodies (only grep hit counts were retrievable in the final iteration) to confirm there is no additional check tying the withdrawn `token` to a specific expected value; the finding is based on the confirmed call chain (`HostManager.onAccept` → `IHostManager(host).withdraw(withdrawParams)`) and the `WithdrawParams.token` field being attacker/governance-supplied per the interface definition. A Devin session with full file access should verify `EvmHost.withdraw`'s implementation directly to close this gap before treating this as fully confirmed.

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L134-147)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L144-158)
```rust
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
```

**File:** modules/pallets/host-executive/src/lib.rs (L182-228)
```rust
		/// Update the host params for the provided state machine
		#[pallet::weight(T::DbWeight::get().writes(1))]
		#[pallet::call_index(1)]
		pub fn update_host_params(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			update: HostParamUpdate,
		) -> DispatchResult {
			T::HostExecutiveOrigin::ensure_origin(origin)?;

			let params = HostParams::<T>::get(&state_machine)
				.ok_or_else(|| Error::<T>::UnknownStateMachine)?;

			let (HostParam::EvmHostParam(mut inner), HostParamUpdate::EvmHostParam(update)) =
				(params.clone(), update);
			inner.update(update);

			let body = inner.abi_encode_with_variant().map_err(|_| Error::<T>::DispatchFailed)?;

			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: inner.host_manager.0.to_vec(),
				timeout: 0,
				body,
			};

			let updated = HostParam::EvmHostParam(inner);

			let dispatcher = <T as Config>::IsmpHost::default();
			dispatcher
				.dispatch_request(
					DispatchRequest::Post(post),
					FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
				)
				.map_err(|_| Error::<T>::DispatchFailed)?;

			HostParams::<T>::insert(state_machine, updated.clone());

			Self::deposit_event(Event::<T>::HostParamsUpdated {
				state_machine,
				old: params,
				new: updated,
			});

			Ok(())
		}
```

**File:** evm/rust/src/host_params.rs (L143-148)
```rust
pub struct EvmHostParamUpdate {
	/// The address of the fee token contract.
	/// It's important that before changing this parameter,
	/// that all funds have been drained from the previous feeToken
	pub fee_token: Option<H160>,
	/// The admin account
```

**File:** evm/src/core/HostManager.sol (L95-109)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
    }
```
