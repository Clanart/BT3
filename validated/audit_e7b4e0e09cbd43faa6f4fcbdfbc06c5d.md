### Title
`HostManager.onAccept` binds only the source *chain id*, not the sending *module identity* — Acknowledged, ENS-style residual trust gap - ([File: evm/src/core/HostManager.sol])

### Summary
`HostManager.onAccept` is the sole entrypoint that lets Hyperbridge-chain governance withdraw bridge revenue or rewrite `HostParams` (fee token, admin, handler, consensusClient, hostManager, challenge period, etc.) on every EVM host. It authorizes the call by checking only that the ISMP request's `source` equals the Hyperbridge parachain id — it never checks `request.from`, i.e. which pallet/module on that chain actually sent the message. [1](#0-0) 

### Finding Description
`updateHostParams` and `withdraw` on `EvmHost` are `restrict`ed to `_hostParams.hostManager` only — a single, trusted address. [2](#0-1) [3](#0-2) 

That trust is delegated entirely to `HostManager.onAccept`, whose only authentication of the incoming cross-chain governance request is:
```solidity
if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
This validates the *chain* the message came from, but not the *module* (`request.from`) that dispatched it. On the Substrate side, `DispatchPost` carries an explicit `from: Vec<u8>` module identifier that is meant to identify the sending pallet (e.g. `host-executive`'s `PALLET_ID`), exactly as documented for cross-chain module binding. [4](#0-3) [5](#0-4) 

Any pallet on the Hyperbridge parachain that is able to dispatch an ISMP POST request to an arbitrary `to` address on an EVM chain (this is a normal, supported pattern — the `pallet-ismp-demo`'s `dispatch_to_evm` extrinsic, callable by any `ensure_signed` origin, freely sets `to: params.module.0.to_vec()` for an EVM destination) can target the `to` = `HostManager` contract address. [6](#0-5) 

If any dispatching pallet's `body` bytes are attacker/user-influenced (the codebase contains several general-purpose cross-chain dispatch pallets — `intents-coprocessor`, `hyper-fungible-token`, `bandwidth` — that build and forward arbitrary bodies), the resulting message decodes cleanly inside `HostManager.onAccept` as `OnAcceptActions.Withdraw` or `OnAcceptActions.SetHostParam` and is forwarded unconditionally to `EvmHost.withdraw`/`updateHostParams`, since `HostManager` never verifies that the message originated specifically from the `host-executive` pallet. [7](#0-6) 

This mirrors exactly the ENS report's core lesson: a single privileged actor (there, the multisig; here, "any message whose `source` is the Hyperbridge chain") is trusted for irreversible, high-impact state changes, and the guarantee that users/relayers assume ("only the host-executive governance pallet can rewrite HostParams or move bridge revenue") is not actually enforced in code — it depends on no other pallet ever being able to reach that `to` address with a matching action byte + payload.

### Impact Explanation
A successful exploitation path would let an unprivileged (non-admin, non-relayer, non-prover) actor:
- Drain all bridge revenue (`feeToken`/native balance) held by `EvmHost` to an attacker-chosen beneficiary via the `Withdraw` action, or
- Overwrite `HostParams` — including `consensusClient`, `handler`, `hostManager`, and `admin` — enabling subsequent false state-commitment acceptance or full host takeover via `SetHostParam`.

Both fall squarely within "stealing or loss of funds," "unauthorized transaction or execution," and "false proof/state acceptance" per the impact gate.

### Likelihood Explanation
The likelihood hinges entirely on whether an existing, currently-deployed pallet gives an unprivileged caller full control of both `to` (destination module address) *and* raw `body` bytes for an EVM-bound POST dispatch. I confirmed the *architecture* permits this (unprivileged `dispatch_to_evm` in `pallet-ismp-demo` controls `to`; several other pallets such as `intents-coprocessor` build and dispatch bodies programmatically), but I could not fully verify within the available index whether any single currently-shipped, unprivileged extrinsic supplies a fully attacker-chosen `body` *together with* an attacker-chosen `to`. This is the piece that would need to be confirmed/patched by the team; the authorization gap itself (missing `from`/module-identity check in `HostManager.onAccept`) is unambiguous and directly verifiable in the cited code.

### Recommendation
`HostManager.onAccept` should authenticate both `request.source` (chain id) **and** `request.from` (module id), checking it against a pinned expected sender (e.g. the `host-executive` pallet's `PALLET_ID`) before decoding and forwarding `Withdraw`/`SetHostParam` actions — mirroring the "module identity" binding requirement already applied elsewhere in the ISMP request/response/timeout paths.

### Proof of Concept
1. On the Hyperbridge parachain, any pallet capable of calling `IsmpDispatcher::dispatch_request` with `DispatchRequest::Post(DispatchPost { dest: StateMachine::Evm(target_chain), from: <any pallet id>, to: <HostManager address on target_chain>, body: [0x00, ...abi_encoded WithdrawParams{beneficiary: attacker, amount: full_balance, token: feeToken}] })` is used.
2. A relayer permissionlessly delivers the message (standard ISMP delivery — no special relayer trust required beyond normal message relay, which is out of scope for "malicious relayer").
3. `EvmHost` invokes `HostManager.onAccept` (restricted to `_params.host`, satisfied by normal delivery flow). [8](#0-7) 
4. The only check performed is `request.source == hyperbridge`; since the message did originate on the Hyperbridge chain (from an arbitrary pallet, not `host-executive`), the check passes.
5. `action = OnAcceptActions.Withdraw` decodes, and `IHostManager(_params.host).withdraw(withdrawParams)` executes, transferring the full bridge revenue balance to the attacker's beneficiary address. [9](#0-8) [3](#0-2)

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

**File:** evm/src/core/EvmHost.sol (L573-576)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }

```

**File:** evm/src/core/EvmHost.sol (L651-660)
```text
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

**File:** modules/pallets/host-executive/src/lib.rs (L199-217)
```rust
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
```

**File:** docs/content/protocol/ismp/dispatcher.mdx (L10-23)
```text
```rust showLineNumbers
/// Simplified POST request, intended to be used for sending outgoing requests
pub struct DispatchPost {
    /// The destination state machine of this request.
    pub dest: StateMachine,
    /// Module identifier of the sending module
    pub from: Vec<u8>,
    /// Module identifier of the receiving module
    pub to: Vec<u8>,
    /// Relative from the current timestamp at which this request expires in seconds.
    pub timeout: u64,
    /// Encoded request body
    pub body: Vec<u8>,
}
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

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L924-946)
```rust
		/// Dispatch a cross-chain message to a gateway contract
		fn dispatch(state_machine: StateMachine, to: H160, body: Vec<u8>) -> DispatchResult {
			// Create dispatcher instance
			let dispatcher = T::Dispatcher::default();

			// Create ISMP post request
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_INTENTS_ID.to_vec(),
				to: to.0.to_vec(),
				timeout: 0, // No timeout for governance actions
				body,
			};

			let dispatch_request = DispatchRequest::Post(post);

			// Create fee metadata with zero fee (no actual fee payment for governance operations)
			let dispatcher_fee = FeeMetadata { payer: [0u8; 32].into(), fee: Zero::zero() };

			// Dispatch via ISMP
			let commitment = dispatcher
				.dispatch_request(dispatch_request, dispatcher_fee)
				.map_err(|_| Error::<T>::DispatchFailed)?;
```
