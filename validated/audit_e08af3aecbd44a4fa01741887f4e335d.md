## Finding: `HostManager.onAccept` authorizes host-reconfiguration and fund-withdrawal actions by chain-of-origin only, never validating the sender module identity (`request.from`)

### Title
Missing sender-module (`request.from`) verification in `HostManager.onAccept` allows any Hyperbridge-chain module to trigger host takeover / fee withdrawal - (File: `evm/src/core/HostManager.sol`)

### Summary
`HostManager.onAccept` gates two highly privileged actions — `withdraw()` (drains accrued protocol/relayer revenue) and `updateHostParams()` (can replace `admin`, `hostManager`, `handler`, `consensusClient`, `feeToken`) — behind a single check: that the incoming ISMP request's `source` chain equals the Hyperbridge state machine id. [1](#0-0) 
It never checks `request.from`, i.e. it never verifies *which module/pallet on the Hyperbridge chain* sent the message. This is the same class of bug as the audited `migrate_state_v1_v2` issue: a privileged state-mutating operation checks the wrong/insufficient identity (chain-level provenance) instead of the correct one (module/role-level provenance), letting an under-privileged sender reach an operation reserved for a specific privileged role.

### Finding Description
Compare `HostManager.onAccept` with the sibling `BandwidthManager`/`pallet-bandwidth` design, which explicitly checks the sender module identity before trusting an inbound message: [2](#0-1) 
and the intended production caller, `pallet-ismp-host-executive`, which dispatches governance messages tagged with its own fixed `PALLET_ID` as `from`: [3](#0-2) 

`HostManager.onAccept`, however, only enforces:
```solidity
if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
and then blindly decodes `request.body` as either a `Withdraw` or `SetHostParam` action and executes it via the host: [1](#0-0) 

`request.source` only proves the message originated from *some* module on the Hyperbridge state machine — it says nothing about *which* module. Any pallet capable of dispatching a `PostRequest` whose destination `to` address and `body` bytes are attacker-influenced (the pattern already exists in-repo, e.g. `pallet-ismp-demo::dispatch_to_evm`, which lets a signed user pick an arbitrary EVM `destination` and arbitrary `to` module address for the outbound request): [4](#0-3) 
would have its message accepted by `HostManager` as if it came from the legitimate governance pallet (`pallet-ismp-host-executive`), because `onAccept` never verifies `request.from` against the expected governance module id.

`EvmHost.updateHostParamsInternal` performs sanity checks on the *new* param values (non-zero handler/consensus/hostManager, valid ERC165 interfaces, non-empty state machines list, min unstaking period) but does not check the caller's authority beyond the `restrict(_hostParams.hostManager)` modifier on `updateHostParams`, which is satisfied by any message that reaches `HostManager.onAccept` and calls back into `IHostManager(_params.host).updateHostParams(...)`: [5](#0-4) 

### Impact Explanation
If any dispatchable path on the Hyperbridge chain lets an unprivileged sender control the `to` (destination module address) and `body` of an outbound `PostRequest`, that sender can forge a `SetHostParam` message to any connected `EvmHost`'s `HostManager` and replace `admin`, `hostManager`, `handler`, and `consensusClient` with attacker-controlled contracts — a full protocol takeover of that chain's bridge endpoint — or forge a `Withdraw` message to drain the host's accrued protocol/relayer fee balance to an attacker address. This satisfies the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" / "false proof or state acceptance" categories, since the vulnerable check accepts a message as authoritative governance input based on the wrong provenance signal.

### Likelihood Explanation
The `onAccept` code itself is unconditionally missing the `request.from` check — this is verifiable directly and does not depend on any compromised relayer, prover, or admin. What is **not fully verified** from the available index is whether a production (non-demo) pallet on the live Hyperbridge runtime exposes a signed, unprivileged extrinsic that lets a caller freely choose both the destination module address (`to`) and the request `body` bytes for a cross-chain dispatch. All production governance dispatchers found (`pallet-ismp-host-executive::update_host_params`, `pallet-bandwidth::dispatch_withdraw`) are gated by `HostExecutiveOrigin`/`AdminOrigin` and construct `body` internally, and `pallet-ismp-demo::dispatch_to_evm` (the one confirmed public/unprivileged path with an attacker-controlled `to`) hardcodes its `body` to a fixed string in the shown code, which would not decode to a valid `SetHostParam`/`Withdraw` action. Full exploitability therefore hinges on the existence of a general-purpose, unprivileged, body-controllable dispatch entrypoint elsewhere in the runtime, which I could not confirm within the indexed code. Regardless, the missing `request.from` authentication in `HostManager.onAccept` is a genuine defense-in-depth gap relative to the pattern used by `BandwidthManager`, and any future or overlooked pallet capable of forwarding attacker-supplied bytes to an arbitrary EVM address would immediately be able to exploit it.

### Recommendation
`HostManager.onAccept` should verify `request.from` equals the well-known governance module id (the SCALE-encoded `PalletId`/`ModuleId` used by `pallet-ismp-host-executive`, analogous to how `pallet-bandwidth::on_accept` checks `request.from == manager.0.to_vec()`), in addition to the existing `request.source` check, before executing `Withdraw` or `SetHostParam`.

### Proof of Concept
Conceptual (structural) PoC, contingent on an unprivileged dispatch path with attacker-controlled `to`/`body` on the Hyperbridge chain:
1. Attacker calls a public, signed extrinsic on the Hyperbridge parachain that dispatches an ISMP `PostRequest` (via `IsmpDispatcher::dispatch_request`), setting `dest` = target EVM chain, `to` = victim chain's `HostManager` address, and `body` = `abi.encodePacked(uint8(1), abi.encode(maliciousHostParams))` (action byte `1` = `SetHostParam`).
2. The dispatcher sets `source = self.host_state_machine()` (Hyperbridge) automatically, satisfying `request.source.equals(hyperbridge())`.
3. Once relayed and proven (a normal, honest relay — no malicious relayer needed), `HostManager.onAccept` is invoked; it checks only `request.source`, decodes `request.body[0]` as `SetHostParam`, and calls `IHostManager(host).updateHostParams(maliciousHostParams)`.
4. `EvmHost.updateHostParamsInternal` accepts the new params (they pass the non-zero/ERC165 checks if attacker deploys contracts satisfying those interfaces) and overwrites `admin`, `hostManager`, `handler`, `consensusClient` — giving the attacker full control of the host, after which they can drain funds via the newly attacker-controlled `hostManager`/`admin`. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L454-465)
```rust
	impl<T: Config> IsmpModule for Pallet<T> {
		fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
			let manager = BandwidthManager::<T>::get(&request.source).ok_or_else(|| {
				anyhow::anyhow!(format!("no bandwidth manager registered for {:?}", request.source))
			})?;

			if request.from != manager.0.to_vec() {
				return Err(anyhow::anyhow!(format!(
					"purchase from unauthorised sender on {:?}: expected {:x?}, got {:x?}",
					request.source, manager.0, request.from
				)));
			}
```

**File:** modules/pallets/host-executive/src/lib.rs (L196-207)
```rust
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
```

**File:** docs/content/developers/polkadot/receiving.mdx (L91-113)
```text
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: Params<T::Balance>) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: EXAMPLE_MODULE_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};

			let dispatcher = T::IsmpDispatcher::default();

			// dispatch the request
            // This call will attempt to collect the protocol fee and relayer fee from the user's account
			dispatcher
				.dispatch_request(
					DispatchRequest::Post(post.clone()),
					FeeMetadata { payer: origin.clone(), fee: params.fee },
				)
				.map_err(|_| Error::<T>::MessageDispatchFailed)?;

			Ok(())
		}
```

**File:** evm/src/core/EvmHost.sol (L573-630)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }

    /**
     * @dev Updates the HostParams. Will reset all fishermen accounts and initialize any new state machines.
     * @param params, the new host params.
     */
    function updateHostParamsInternal(HostParams memory params) internal {
        // check the params to prevent the host from getting bricked.
        if (
            params.hostManager == address(0) || address(params.hostManager).code.length == 0
                || !IERC165(params.hostManager).supportsInterface(type(IApp).interfaceId)
        ) {
            // otherwise cannot process new cross-chain governance requests
            revert InvalidHostManager();
        }

        if (
            params.handler == address(0) || address(params.handler).code.length == 0
                || !IERC165(params.handler).supportsInterface(type(IHandlerV2).interfaceId)
        ) {
            // otherwise cannot process new datagrams
            revert InvalidHandler();
        }

        if (
            params.consensusClient == address(0) || address(params.consensusClient).code.length == 0
                || !IERC165(params.consensusClient).supportsInterface(type(IConsensusV2).interfaceId)
        ) {
            // otherwise cannot process new consensus datagrams
            revert InvalidConsensusClient();
        }

        // otherwise cannot process new cross-chain governance requests
        if (keccak256(params.hyperbridge) == keccak256(bytes(""))) revert InvalidHyperbridgeId();

        // otherwise cannot process new datagrams
        uint256 stateMachinesLen = params.stateMachines.length;
        if (stateMachinesLen == 0) revert InvalidStateMachinesLength();

        // otherwise cannot process new datagrams
        if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();

        address oldFeeToken = feeToken();
        if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
            uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
            if (balance != 0) revert CannotChangeFeeToken();
        }

        // safe to emit here because invariants have already been checked
        // and don't want to store a temp variable for the old params
        emit HostParamsUpdated({oldParams: _hostParams, newParams: params});

        _hostParams.feeToken = params.feeToken;
        _hostParams.admin = params.admin;
        _hostParams.handler = params.handler;
        _hostParams.hostManager = params.hostManager;
```
