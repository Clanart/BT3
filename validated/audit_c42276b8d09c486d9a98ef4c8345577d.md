## Analysis

The seed bug (M-23) is a **cache/authority decoupling** pattern: an address that authorizes/receives protocol actions is changed on one side of a system without the corresponding "mirror" of that address being updated in lock-step, so subsequent privileged calls are routed to (or authorized from) a stale address, and either control or funds are lost.

Hyperbridge has a structurally identical decoupling in the `pallet-ismp-host-executive` → `EvmHost.hostManager` relationship.

### The mirror
`HostParams::<T>` in `modules/pallets/host-executive/src/lib.rs` is Hyperbridge's local mirror of each `EvmHost`'s `HostParams`, including `host_manager` — the address on the EVM chain that is authorized (via `restrict(_hostParams.hostManager)`) to call `EvmHost.updateHostParams()` / `withdraw()`. [1](#0-0) 

### The break
`update_host_params` mutates the *local copy* first (`inner.update(update)`), then builds the outbound `DispatchPost` using the **already-mutated** `inner.host_manager` as the routing target, and finally writes the mutated struct into storage **unconditionally**, regardless of whether the ISMP dispatch ever succeeds on the destination chain: [2](#0-1) 

If the update changes `host_manager` itself (a legitimate governance operation — replace/redeploy a `HostManager`), the request is routed `to: inner.host_manager` (the **new** address), not to the currently-authorized `EvmHost.hostParams().hostManager` (the **old** address). On the EVM side, `EvmHost.updateHostParams`/`withdraw` are `restrict(_hostParams.hostManager)`-gated to the *old* address: [3](#0-2) [4](#0-3) 

So the message never reaches (or is authorized by) the real host — the on-chain `hostManager` never actually changes — while Hyperbridge's `HostParams` storage has already committed to the new value.

### Downstream impact
`pallet-ismp-relayer`'s fee withdrawal path reads this same mirrored `host_manager` to route relayer-fee payouts: [5](#0-4) 

Once the mirror is desynced, every subsequent relayer fee withdrawal for that state machine is dispatched to the wrong `to` address on the EVM side. It either lands on a contract that isn't the real host's authorized `hostManager` (so the real `EvmHost.withdraw()`/`updateHostParams()` call inside it reverts with `UnauthorizedAction`) or lands on an address with no code — either way, relayer fees for that chain become permanently unwithdrawable, and there is no code path to resynchronize `HostParams::<T>` back to the real on-chain value.

### Title
Host-executive pallet updates its `HostParams` mirror before the corresponding `EvmHost.updateHostParams` message is confirmed delivered, permanently desyncing `host_manager` and bricking relayer-fee withdrawal — (File: `modules/pallets/host-executive/src/lib.rs`)

### Summary
`update_host_params` writes the post-update `HostParam` (including a changed `host_manager`) into `HostParams::<T>` storage optimistically, and separately routes the governance `DispatchPost` to the *new* `host_manager` address instead of the currently-authorized one. Because `EvmHost.updateHostParams`/`withdraw` are gated by `restrict(_hostParams.hostManager)` against the *old* address, the on-chain update is never actually applied when the request reaches the new address, yet Hyperbridge's local mirror now disagrees with on-chain truth permanently.

### Finding Description
- `update_host_params` mutates `inner` first via `inner.update(update)`, then uses `inner.host_manager` (post-mutation) as the ISMP `to` field. [6](#0-5)  
- `HostParams::<T>::insert(state_machine, updated.clone())` commits the new value to storage unconditionally, with no confirmation that the on-chain `EvmHost` actually accepted the update. [7](#0-6) 
- On the EVM side, both `updateHostParams` and `withdraw` are restricted to the *current* `_hostParams.hostManager`. [3](#0-2) [4](#0-3) 
- `HostManager.onAccept` itself is gated to `restrict(_params.host)` and forwards the call unconditionally to `IHostManager(_params.host)`, so if the request lands on a *new*, not-yet-authorized `HostManager` contract, the inner call to the real host's `updateHostParams`/`withdraw` fails the `restrict` check. [8](#0-7) 
- The relayer pallet's withdrawal path blindly trusts the mirrored value for routing payouts. [5](#0-4) 

### Impact Explanation
Once desynced, relayer fee withdrawals for the affected `state_machine` are routed to an address that the real `EvmHost` never actually authorized, causing withdrawal transactions to revert on-chain or land on the wrong/no-op contract. This is a fund-lock condition: accrued relayer fees (`Fees::<T>`) for that destination become unwithdrawable, and there's no extrinsic to re-point `HostParams::<T>` back without re-running the same broken update path.

### Likelihood Explanation
This triggers on any ordinary `update_host_params` call that rotates `host_manager` (a supported, expected operational action — e.g. redeploying `HostManager`) without special malicious intent; it doesn't need a compromised key or relayer, only the ordinary use of an existing, documented pallet call whose implementation writes state ahead of confirmed cross-chain delivery.

### Recommendation
Do not write `HostParams::<T>` optimistically. Either (a) route the update `DispatchPost.to` using the *pre-update* `host_manager` (the currently authorized address) rather than the post-update value, and/or (b) defer the storage mutation until an ISMP response/receipt confirms the destination `EvmHost` applied the change, mirroring the pattern used for the bandwidth manager's one-shot `setHost` binding.

### Proof of Concept
1. Governance calls `pallet_ismp_host_executive::update_host_params(state_machine, HostParamUpdate::EvmHostParam(EvmHostParamUpdate { host_manager: Some(new_manager), .. }))`.
2. Pallet computes `inner.host_manager = new_manager`, builds `DispatchPost { to: new_manager.0.to_vec(), .. }`, dispatches it, and immediately stores the updated params — `HostParams::<T>` now shows `new_manager`. [9](#0-8) 
3. On EVM, the message is delivered to `new_manager.onAccept`, which calls `IHostManager(EvmHost).updateHostParams(...)`; `EvmHost` reverts because `msg.sender == new_manager != _hostParams.hostManager (old_manager)`. [3](#0-2) 
4. The real `EvmHost.hostParams().hostManager` remains `old_manager` forever; Hyperbridge's `HostParams::<T>` permanently shows `new_manager`.
5. Any relayer calling `withdraw_fees` for this `state_machine` gets a `WithdrawalParams` message routed `to: new_manager`, which cannot successfully call the real host's `withdraw()` — fees are stuck. [5](#0-4)

### Citations

**File:** modules/pallets/host-executive/src/lib.rs (L74-83)
```rust
	/// Host Params for all connected chains
	#[pallet::storage]
	#[pallet::getter(fn host_params)]
	pub type HostParams<T: Config> =
		StorageMap<_, Twox64Concat, StateMachine, HostParam, OptionQuery>;

	/// EvmHost addresses of all connected Evm chains
	#[pallet::storage]
	#[pallet::getter(fn evm_hosts)]
	pub type EvmHosts<T: Config> = StorageMap<_, Twox64Concat, StateMachine, H160, OptionQuery>;
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

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

**File:** evm/src/core/EvmHost.sol (L651-651)
```text
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
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

**File:** evm/src/core/HostManager.sol (L95-108)
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
```
