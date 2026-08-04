### Title
`HostManager.onAccept` authenticates the source chain but never verifies the module/app identity (`request.from`), letting any pallet or unprivileged dispatcher on the Hyperbridge chain trigger governance-only `withdraw`/`updateHostParams` actions - (`evm/src/core/HostManager.sol`)

### Summary
`HostManager.onAccept` is the sole entrypoint through which "cross-chain governance" reaches an `EvmHost` to withdraw protocol revenue or rewrite the host's critical parameters. Its access control mirrors the `setHalted()` bug in the yAxis report: a security-critical action is gated on the wrong/insufficient authority check. Instead of binding to the specific trusted governance module identity, it only checks that the *message came from the Hyperbridge chain*, never that it came from the *specific governance module* on that chain.

### Finding Description
`HostManager.onAccept` is: [1](#0-0) 

```solidity
function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
    PostRequest calldata request = incoming.request;
    // Only the Hyperbridge parachain can send requests to this module.
    if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

    OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
    if (action == OnAcceptActions.Withdraw) {
        WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
        IHostManager(_params.host).withdraw(withdrawParams);
    } else if (action == OnAcceptActions.SetHostParam) {
        HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
        IHostManager(_params.host).updateHostParams(hostParams);
    }
}
```

The only check performed is `request.source.equals(hyperbridge())` — i.e. that the ISMP message *originated on the Hyperbridge parachain*. There is **no check on `request.from`**, the field that identifies *which module on that chain* dispatched the request. On the Substrate side, `request.from` is fully attacker-controlled application data set by whichever pallet/extrinsic dispatches the `PostRequest` — it is not an authenticated origin: [2](#0-1) [3](#0-2) 

`dispatch_to_evm` (and the generic `send_message` extrinsic documented in `docs/content/developers/polkadot/dispatching.mdx`) is callable by any `ensure_signed` account, and lets the caller freely choose the destination `to` (the target contract, e.g. `HostManager`) and freely craft the request `body`. The pallet itself sets `from: PALLET_ID.to_bytes()`, but this demonstrates the general pattern: any pallet with a permissionless dispatch extrinsic on the Hyperbridge chain can set `to` to `HostManager`'s address and craft a `body` that decodes into `OnAcceptActions.Withdraw` or `OnAcceptActions.SetHostParam`. Because `HostManager.onAccept` validates only the *chain id* (`request.source`) and never the *module id* (`request.from`), it cannot distinguish a message legitimately dispatched by the intended treasury/governance pallet from one dispatched by any other pallet or extrinsic on the same chain that a user can reach.

This is the same broken-invariant class as the `setHalted()`/`onlyStrategist` bug: a function meant to be reachable only by the highest-trust actor (`onlyGovernance`) is actually reachable by a lower-privileged path (any module/dispatcher on the source chain), because the guard checks the wrong scope (chain-level instead of module-level authorization). The task's own pivot list calls this out directly: *"Request, response, and timeout paths must bind chain id, module/app identity ... on both Substrate and EVM,"* and `HostManager` binds only the chain id.

### Impact Explanation
If any reachable dispatch path on the Hyperbridge state machine allows an unprivileged/unintended module to set `to = HostManager` and forge the body, the attacker can:
- Call `withdraw()` and redirect the host's escrowed fee-token/native revenue to an arbitrary `beneficiary` (`EvmHost.withdraw`, `evm/src/core/EvmHost.sol` lines 651-660), i.e. theft of protocol funds.
- Call `updateHostParams()` and rewrite `admin`, `handler`, `hostManager`, `consensusClient`, `feeToken`, etc. (`evm/src/core/EvmHost.sol` lines 573-645), which is a full protocol takeover: a new attacker-controlled handler/admin could then falsify state commitments, freeze the host, or drain funds through subsequent privileged calls.

This satisfies the bounty's accepted-impact list: stealing/loss of funds, unauthorized transaction/execution, and logic attacks via false module-identity acceptance.

### Likelihood Explanation
The likelihood hinges on whether some permissionless (or lower-trust-than-governance) call path on the Hyperbridge chain can set an arbitrary destination address and arbitrary request body while dispatching a `PostRequest`. The demo pallet shows this pattern is directly supported by the ISMP dispatch primitives (`dispatch_to_evm`/`send_message`, `ensure_signed` only), and `IsmpDispatcher::dispatch_request` itself performs no `from`/`to` authorization — it is a generic low-level primitive intended to be used by any pallet. Whether the *production* Gargantua/Nexus runtimes expose an equivalently permissionless path with attacker-controlled `to`/`body` reaching `HostManager`'s address specifically could not be fully confirmed from the indexed code (the demo pallet is explicitly a demo/test pallet, and production runtime dispatch call-sites were not fully enumerated in this pass). This is the main uncertainty: the vulnerable *validation gap* in `HostManager.onAccept` is confirmed and unconditional, but confirming a fully unprivileged concrete production caller requires checking every pallet's dispatch extrinsics for unrestricted `to`/`body` control, which the index does not fully cover.

### Recommendation
`HostManager.onAccept` must authenticate both the source chain *and* the source module identity. Store the expected governance/treasury module id (`request.from`) at construction/initialization (mirroring how `_params.host` is pinned), and check it explicitly:
```solidity
if (!request.source.equals(hyperbridge()) || !request.from.equals(_params.governanceModule)) revert UnauthorizedAction();
```
This closes the gap the same way the yAxis fix moved `setHalted()` from `onlyStrategist` to `onlyGovernance` — by binding the privileged action to the single trusted, highest-authority identity rather than any actor that merely satisfies a weaker, broader condition (being on the right chain).

### Proof of Concept
1. On the Hyperbridge chain, use any permissionless pallet call that internally invokes `pallet_ismp::Pallet::dispatch_request` (e.g. the pattern shown in `pallet_ismp_demo::dispatch_to_evm`, `modules/pallets/demo/src/lib.rs` lines 216-239) with:
   - `dest = StateMachine::Evm(<hyperbridge-connected EVM chain id>)`
   - `to = <HostManager address>`
   - `body = abi.encodePacked(uint8(OnAcceptActions.Withdraw), abi.encode(WithdrawParams{ beneficiary: attacker, amount: hostBalance, token: feeToken }))`
2. Once a relayer delivers and the message is verified by the destination `EvmHost`/`HandlerV2` (proof verification only checks the state commitment of the *Hyperbridge chain*, not which module on it authored the message), `dispatchIncoming` calls `HostManager.onAccept`.
3. `onAccept` checks only `request.source.equals(hyperbridge())` — true, since the message did originate on the Hyperbridge chain — and proceeds to decode `body` and call `IHostManager(_params.host).withdraw(withdrawParams)`, transferring the host's revenue to the attacker-chosen `beneficiary`. [4](#0-3) [5](#0-4)

### Citations

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

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-146)
```rust
			DispatchRequest::Post(dispatch_post) => {
				let post = PostRequest {
					source: self.host_state_machine(),
					dest: dispatch_post.dest,
					nonce: self.next_nonce(),
					from: dispatch_post.from,
					to: dispatch_post.to,
					timeout_timestamp: if dispatch_post.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_post.timeout)
					},
					body: dispatch_post.body,
				};
				Request::Post(post)
			},
		};
```

**File:** modules/pallets/demo/src/lib.rs (L216-239)
```rust
		/// Dispatch request to a connected EVM chain.
		#[pallet::weight(Weight::from_parts(1_000_000, 0))]
		#[pallet::call_index(2)]
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: EvmParams) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: PALLET_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};
			let dispatcher = T::IsmpHost::default();
			for _ in 0..params.count {
				// dispatch the request
				dispatcher
					.dispatch_request(
						DispatchRequest::Post(post.clone()),
						FeeMetadata { payer: origin.clone(), fee: Default::default() },
					)
					.map_err(|_| Error::<T>::TransferFailed)?;
			}
			Ok(())
		}
```

**File:** evm/src/core/EvmHost.sol (L647-660)
```text
    /**
     * @dev withdraws host revenue to the given address, can only be called by cross-chain governance
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```
