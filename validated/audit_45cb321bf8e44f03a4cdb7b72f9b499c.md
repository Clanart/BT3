# Missing Source-Module Binding in `HostManager.onAccept` — Chain-Level Authentication Without Module-Level Authentication

### Title
Missing source-module authentication allows any Hyperbridge-chain pallet/message to impersonate the privileged `pallet-ismp-host-executive` governance module - (File: `evm/src/core/HostManager.sol`)

### Summary
`HostManager.onAccept` only verifies that an inbound `PostRequest.source` equals the configured Hyperbridge state machine id. It never checks `request.from` against the specific module identity (`PALLET_ID`) of `pallet-ismp-host-executive`, the only module that is supposed to be allowed to issue `Withdraw`/`SetHostParam` governance actions.

### Finding Description
`HostManager.onAccept` authenticates the request with a single check: [1](#0-0) 

Note that it checks `request.source.equals(IHost(_params.host).hyperbridge())` — the *chain* the message came from — but at no point decodes or compares `request.from` (the *module* that dispatched the message on that chain) to any expected value.

This is a materially weaker pattern than what other apps in the same codebase use for the same class of trust decision. `ExtrinsicIntents._authenticate` binds *both* the source chain and the exact sender module before trusting a payload: [2](#0-1) 

On the Hyperbridge-chain side, the intended sole issuer of governance actions is `pallet-ismp-host-executive`, whose `withdraw` and `update_host_params` extrinsics are gated by `HostExecutiveOrigin` and always stamp `from: PALLET_ID.to_bytes()`: [3](#0-2) [4](#0-3) 

However, the generic `pallet-ismp` `IsmpDispatcher::dispatch_request` implementation places whatever `from`/`to`/`body` the *calling pallet* supplies into the outgoing `PostRequest`, with no validation that `from` actually corresponds to the calling pallet's true identity: [5](#0-4) 

The documented pattern for building pallets on top of Hyperbridge explicitly shows a signed, unprivileged extrinsic accepting a full, caller-supplied `DispatchPost` (including `from` and `to`) and forwarding it verbatim to the dispatcher: [6](#0-5) 

Because `request.source` is always stamped as `self.host_state_machine()` (i.e., the real Hyperbridge chain) regardless of which pallet or account initiated the dispatch, any pallet on the Hyperbridge/Nexus runtime that forwards a user- or pallet-controlled `from`/`to`/`body` (following the documented pattern above) can produce a `PostRequest` that:
- has `source == hyperbridge` (satisfies `HostManager`'s only check), 
- has `to == HostManager` address on the target EVM chain,
- has `from` set to anything the caller chooses (e.g., spoofing `PALLET_ID` of `hostexec` or simply irrelevant, since `HostManager` never checks it), and
- carries an attacker-chosen `body` selecting `OnAcceptActions.Withdraw` or `OnAcceptActions.SetHostParam` with attacker-chosen beneficiary/amount/params.

`onAccept` will decode and execute this as legitimate governance, calling `IHostManager(_params.host).withdraw(...)` or `updateHostParams(...)` without ever verifying that the true source module was `pallet-ismp-host-executive`.

### Impact Explanation
If any Hyperbridge-chain pallet's dispatch path allows the `from` field to be attacker-influenced (directly, or indirectly via a compromised/careless third-party pallet integrated into the runtime), the missing module binding lets that path forge privileged cross-chain governance: draining the fee-token/native treasury via `Withdraw`, or rewriting `HostParams` (admin, handler, consensusClient, hostManager, challengePeriod, etc.) via `SetHostParam` on any connected EVM chain. This is unauthenticated cross-chain host-management execution — Critical severity as stated in the target.

### Likelihood Explanation
Exploitability is conditional on the existence, in the production Nexus/Gargantua runtime, of a pallet exposing a permissionless (or under-restricted) extrinsic that forwards caller-controlled `from`/`to`/`body` into `IsmpDispatcher::dispatch_request` — a pattern that is explicitly documented and demonstrated in this repo's own pallet examples (`docs/content/developers/polkadot/dispatching.mdx`, `modules/pallets/demo/src/lib.rs::dispatch_to_evm`). I was not able to confirm from the indexed content whether the actual production Nexus/Gargantua runtime pallet set includes such an unrestricted, user-facing raw-dispatch extrinsic reachable by unprivileged signers — this would need to be verified directly against `parachain/runtimes/nexus` and `parachain/runtimes/gargantua` pallet configurations and any user-facing "generic message" pallets they register. Regardless of that runtime-composition question, the `HostManager.sol` code itself is defective in isolation: it deviates from the module-binding pattern used elsewhere in the same codebase (`ExtrinsicIntents._authenticate`) and provides no defense-in-depth against a mis-configured or future pallet on the Hyperbridge chain that might relay attacker-controlled `from` values.

### Recommendation
Add an explicit module-identity check in `HostManager.onAccept`, mirroring `ExtrinsicIntents._authenticate`: decode `request.from` and require it to equal the known `PALLET_ID` bytes of `pallet-ismp-host-executive` (store this as an immutable/configurable parameter on `HostManager`, analogous to `_instance(request.source)` for `IntentGateway`), rejecting with `UnauthorizedAction` otherwise. This closes the gap regardless of what other pallets on the Hyperbridge chain do with their own dispatch permissions.

### Proof of Concept
1. On the Hyperbridge/Nexus chain, identify or deploy a pallet that exposes a signed, non-privileged extrinsic forwarding a caller-supplied `DispatchPost { dest, from, to, timeout, body }` to `IsmpDispatcher::dispatch_request` (pattern shown in `docs/content/developers/polkadot/dispatching.mdx` lines 57-76 / `modules/pallets/demo/src/lib.rs` `dispatch_to_evm`).
2. From an unprivileged signer, call that extrinsic with:
   - `dest = StateMachine::Evm(<target chain id>)`
   - `to = <HostManager address on target chain>`
   - `from = <arbitrary bytes, e.g. PALLET_ID of hostexec>`
   - `body = abi.encodePacked(uint8(OnAcceptActions.Withdraw), abi.encode(WithdrawParams{ beneficiary: attacker, amount: max, token: feeToken }))`
3. Once relayed and proven on the destination `EvmHost`, `HandlerV2.handlePostRequests` routes to `HostManager.onAccept`.
4. `onAccept` checks only `request.source == hyperbridge` (true), decodes the action, and calls `IHostManager(_params.host).withdraw(withdrawParams)`, draining protocol funds to the attacker beneficiary — despite `request.from` never being validated against the legitimate `hostexec` `PALLET_ID`.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** modules/pallets/host-executive/src/lib.rs (L201-207)
```rust
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: inner.host_manager.0.to_vec(),
				timeout: 0,
				body,
			};
```

**File:** modules/pallets/host-executive/src/lib.rs (L292-298)
```rust
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: params.host_manager.0.to_vec(),
				timeout: 0,
				body: data,
			};
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

**File:** docs/content/developers/polkadot/dispatching.mdx (L57-76)
```text
```rust showLineNumbers
#[pallet::weight(T::dispatch())]
#[pallet::call_index(0)]
pub fn send_message(
    origin: OriginFor<T>,
    post: DispatchPost,
    fee: T::Balance,
) -> DispatchResultWithPostInfo {
    let signer = ensure_signed(origin)?;
    let dispatcher = pallet_ismp::Pallet::<Runtime>::default();
    let commitment = dispatcher.dispatch_request(
        DispatchRequest::Post(post),
        FeeMetadata {
            payer: signer,
            fee,
        }
    )?;

    Ok(())
}
```
