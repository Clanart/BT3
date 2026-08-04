Based on my investigation, I found a real analog: `HostManager.onAccept` on EVM chains authenticates governance messages by chain identity alone, never by module identity, unlike the report's core complaint that a privileged action lacked binding to the specific authorized caller.

### Title
HostManager.onAccept authenticates by source chain only, not by sending module — any Hyperbridge-chain module can trigger withdrawals/param changes - (File: evm/src/core/HostManager.sol)

### Summary
`HostManager.onAccept` is the sole gate for EVM-side bridge-revenue withdrawals and host-parameter updates. It checks that the ISMP request's `source` chain equals the Hyperbridge state machine, but it never checks `request.from` — the identifier of the specific pallet/module on the Hyperbridge chain that dispatched the message. This is the same class of missing-authorization-account bug as the reported `dtf_program_signer` issue: a coarse, insufficiently-specific check stands in for a required per-caller authorization check.

### Finding Description
`HostManager.onAccept` decodes an `OnAcceptActions` byte and dispatches either `withdraw` (drains fee-token/native balance to an attacker-chosen beneficiary) or `updateHostParams` (rewrites `admin`, `handler`, `consensusClient`, etc.) based solely on: [1](#0-0) 

The only check performed is: [2](#0-1) 

This validates the *chain* the message came from (Hyperbridge) but not the *module* on that chain (`request.from`). On the Hyperbridge side, `pallet-ismp`'s `IsmpDispatcher::dispatch_request` lets any pallet freely set `from` to its own `PALLET_ID` — this is attacker-uncontrolled only insofar as the calling code is trusted, but the field itself carries no cryptographic binding enforced on the EVM side: [3](#0-2) 

Compare this to `BandwidthManager`/`pallet-bandwidth`, which explicitly enforces the module-level check that `HostManager` is missing: "The pallet rejects any purchase whose `request.from` doesn't equal the address stored under `BandwidthManager<T>::get(request.source)`" — i.e., a per-source-chain authorized-sender allowlist: [4](#0-3) 

`HostManager` has no equivalent allowlist of which Hyperbridge-chain module id is authorized to issue `Withdraw`/`SetHostParam` actions. Any pallet or extrinsic on the Hyperbridge chain capable of dispatching an ISMP POST to `to = HostManager` with `body[0] ∈ {0,1}` and a correctly ABI-encoded `WithdrawParams`/`HostParams` payload — regardless of which pallet's `from` is set — will be accepted, exactly mirroring the report's exploit primitive: an unrelated caller impersonating the intended privileged sender because the check validates the wrong scope (chain instead of module/account).

### Impact Explanation
If any Hyperbridge-chain pallet exposes (now or in the future) a call that lets a caller control `to` and `body` of an outgoing POST request even partially (e.g. any new pallet reusing the generic `dispatch_to_evm` pattern seen in `pallet-ismp-demo`, which already lets a *signed, non-privileged* user pick the destination `to` address freely), the missing `from` check on `HostManager` collapses the entire governance-authorization model down to "came from the Hyperbridge chain," letting that call target `HostManager` and drain protocol/relayer revenue (`withdraw`) or overwrite `HostParams` (`admin`, `handler`, `consensusClient` — a full host takeover), matching the required "stealing or loss of funds" / "unauthorized execution" impact classes. [5](#0-4) 

### Likelihood Explanation
The check itself is unconditionally present today in every dispatch path I inspected (`pallet-host-executive::withdraw`/`update_host_params`, `pallet-relayer::withdraw`) which correctly emit governance-authorized `from` values via `AdminOrigin`-gated or protocol-internal pallets: [6](#0-5) [7](#0-6) 
This lowers immediate exploitability, since I could not confirm an existing, currently-shipped, unprivileged pallet call that lets an ordinary signed account set an arbitrary `body` (only `to`/destination is user-controlled in the demo pallet, with `body` hardcoded to `b"Hello from polkadot"`). However, the *guard itself* is missing defense-in-depth: correctness depends entirely on every present and future Hyperbridge-chain pallet never exposing a route where `to`/`body` are jointly attacker-influenced — a much weaker invariant than checking `request.from` against an explicit allowlist (as `pallet-bandwidth` already does for the reverse direction). This mirrors the report's "Low difficulty" framing: exploitability today is gated by the absence of a convenient calling primitive, not by any authorization check.

### Recommendation
- **Short term:** Add an explicit check in `HostManager.onAccept` that `request.from` equals a configured, single authorized Hyperbridge-side module id (e.g., `pallet-ismp-host-executive`'s `PALLET_ID`), stored in `HostManagerParams`, mirroring the allowlist pattern already used by `pallet-bandwidth`.
- **Long term:** Audit every EVM contract implementing `IApp.onAccept` for governance-style actions (`HostManager`, `SimplexPaymaster`, `BandwidthManager`, `ExtrinsicIntents`) to ensure each checks both `request.source` (chain) and `request.from` (module), not chain alone.

### Proof of Concept
Not independently exploitable with only the code seen so far — no currently-shipped unprivileged Hyperbridge-chain call was found that lets a caller set an arbitrary `body` targeting `HostManager` (the closest example, `pallet-ismp-demo::dispatch_to_evm`, hardcodes the body). The concrete exploit path therefore depends on either (a) an unaudited/future pallet reusing that free-`to` pattern with a free `body`, or (b) any pallet bug that lets `from`/`to`/`body` be jointly attacker-controlled. I flag this as a structural authorization gap analogous to the report rather than a fully demonstrated live exploit; confirming exploitability further would require enumerating all currently-deployed Hyperbridge-chain extrinsics for a free-body dispatch path, which was not fully explorable within the indexed subset of the repository.

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

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-145)
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
```

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L16-17)
```text
- **Pallet → manager** (outbound governance). `BandwidthManager.onAccept` checks `request.source == IDispatcher(_host).hyperbridge()`. Only messages dispatched from Hyperbridge are honored. The `to` field is the manager's address — no module-id lookup.
- **Manager → pallet** (inbound purchases). The pallet rejects any purchase whose `request.from` doesn't equal the address stored under `BandwidthManager<T>::get(request.source)`. An attacker who deploys their own contract on a source chain cannot mint subscriptions.
```

**File:** modules/pallets/demo/src/lib.rs (L216-226)
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
```

**File:** modules/pallets/host-executive/src/lib.rs (L273-298)
```rust
		/// Issues a call to withdraw the protocol fees from an evm chain
		#[pallet::weight(T::DbWeight::get().writes(1))]
		#[pallet::call_index(4)]
		pub fn withdraw(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			withdrawal_params: WithdrawalParams,
		) -> DispatchResult {
			T::HostExecutiveOrigin::ensure_origin(origin)?;

			ensure!(state_machine.is_evm(), Error::<T>::UnsupportedStateMachine);

			let HostParam::EvmHostParam(params) = HostParams::<T>::get(state_machine)
				.ok_or_else(|| Error::<T>::UnknownStateMachine)?;

			let data = withdrawal_params
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidBeneficiaryAddress)?;

			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: params.host_manager.0.to_vec(),
				timeout: 0,
				body: data,
			};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-167)
```rust
		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};
```
