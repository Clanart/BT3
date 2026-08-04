Based on the code gathered, the strongest local analog is in `HostManager.sol`'s cross-chain governance intake, which authenticates the *source chain* of an incoming governance request but never authenticates the *source module* on that chain.

### Title
HostManager accepts governance actions from any Hyperbridge-side module, not just an authorized governance module - (File: evm/src/core/HostManager.sol)

### Summary
`HostManager.onAccept` is the single entry point through which `EvmHost.updateHostParams` and `EvmHost.withdraw` can be triggered from the Hyperbridge chain. It checks only that the incoming `PostRequest.source` equals the Hyperbridge state machine; it never checks `PostRequest.from` (the sending module/pallet identifier) against any allowlist of trusted governance modules.

### Finding Description
`HostManager.onAccept` decodes the request body into `OnAcceptActions::Withdraw` or `OnAcceptActions::SetHostParam` and forwards it directly to the privileged `EvmHost.withdraw` / `EvmHost.updateHostParams` functions, which are `restrict`ed to `_hostParams.hostManager` (i.e., this very contract) [1](#0-0) . The only authentication performed is:

```solidity
if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
``` [2](#0-1) 

This validates the request's *source chain* (the Hyperbridge parachain) but does nothing with `request.from`, the module identifier of whichever pallet on Hyperbridge actually dispatched the message. Contrast this with `EvmHost.updateHostParamsInternal`, whose own comment states the admin has *no* privileges here and governance flows exclusively through `hostManager` [3](#0-2) , i.e. the entire trust model for host-param mutation and revenue withdrawal is delegated to whatever `HostManager.onAccept` decides to authorize.

The codebase's own design elsewhere shows that `from` (module id) is understood to be the correct authorization boundary for distinguishing trusted system pallets from arbitrary ones — e.g. the outbound-reward allowlist is explicitly keyed by `request.from` because "a module with zero reward is treated as not on the allowlist" [4](#0-3) . `HostManager` skips this module-level check entirely for governance-critical actions (fee-token changes, consensus-client swap, `hostManager` rotation, and outright fund withdrawal via `WithdrawParams`).

Any pallet on the Hyperbridge chain capable of dispatching an ISMP `PostRequest` — via `IsmpDispatcher::dispatch_request` with `to` set to the destination `HostManager` address and an attacker-shaped `body` — is accepted, since `dispatch_request` only requires a `DispatchPost{ dest, from, to, timeout, body }` and imposes no restriction on `from`/`to`/`body` at the dispatcher level [5](#0-4) ; enforcement is expected downstream, and here it is missing.

### Impact Explanation
If any non-governance pallet or future/self-service pallet on Hyperbridge (or a message routed with an arbitrary `from`) can reach `dispatch_request` targeting a registered `HostManager` contract with a crafted body, it can:
- Call `IHostManager.withdraw` to drain the EVM host's accumulated fee-token/native revenue to an attacker-chosen `beneficiary` (`WithdrawParams.beneficiary`/`amount`), i.e., direct fund theft [6](#0-5) .
- Call `IHostManager.updateHostParams` to rewrite `consensusClient`, `handler`, `hostManager`, `feeToken`, or the whitelisted `stateMachines`, i.e., false-state/consensus takeover of the host, since these are exactly the parameters gating proof/state acceptance [7](#0-6) .

This is a strictly stronger and more concrete instance of the report's "owner power" concern: instead of a legitimate but centralized multisig owner, the actual bug is that the authorization check is *missing a dimension* (module identity), so the privileged path is reachable by an unintended, potentially unprivileged, actor.

### Likelihood Explanation
Exploitability depends on whether any reachable code path on the live Hyperbridge chain can dispatch an ISMP `PostRequest` with attacker-controlled `to` and `body` fields (not hardcoded, as in the ping/demo pallet's `dispatch_to_evm`, whose body is fixed to `b"Hello from polkadot"` [8](#0-7) ). I was not able to confirm within the indexed code whether a currently-deployed, user-reachable pallet grants that level of control over `to`/`body`, or whether it is confined to trusted system pallets whose `from` and payload are fixed at compile time (host-executive, intents-coprocessor, token-governor, relayer). This is the main open uncertainty; a full audit of every `IsmpDispatcher::dispatch_request` call site across `modules/pallets/*` (and any runtime-added application pallets) is needed to establish concrete reachability by an unprivileged account. Regardless, the missing `request.from` check in `HostManager.onAccept` is a real, provable gap in defense-in-depth versus the design already used elsewhere in the codebase (module-id allowlisting in `pallet-relayer`'s outbound reward system).

### Recommendation
Add an explicit module-identity check in `HostManager.onAccept`, mirroring the `OutboundRequestDeliveryReward` allowlist pattern: require `request.from` to equal a configured, immutable (or governance-rotatable) trusted module id — e.g. the `pallet-ismp-host-executive` module id — before decoding and forwarding `Withdraw`/`SetHostParam` actions. This restores the two-dimensional authentication (source chain + source module) that the rest of the protocol already relies on for security-critical cross-chain flows.

### Proof of Concept
Not fully constructible from the indexed code alone, since the concrete unprivileged entrypoint on the Hyperbridge chain with attacker-controlled `to`/`body` could not be confirmed with certainty in this pass (see Likelihood Explanation). The structural PoC is:
1. Any pallet (or a message whose `from` is not the intended governance module id) calls `IsmpDispatcher::dispatch_request` with `dest = Evm(<target chain>)`, `to = <HostManager address>`, `body = 0x01 || abi.encode(WithdrawParams{ beneficiary: attacker, amount: hostBalance, token: feeToken })`.
2. Once delivered and proven on the destination via `HandlerV2`, `EvmHost.dispatchIncoming` invokes `HostManager.onAccept`.
3. `onAccept` checks only `request.source == hyperbridge` (true, since the message genuinely originated on Hyperbridge) and proceeds to decode `OnAcceptActions.Withdraw`, calling `EvmHost.withdraw`, transferring the host's revenue to the attacker's `beneficiary` — without ever verifying that `request.from` was the legitimate governance/host-executive module. [1](#0-0)

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

**File:** evm/src/core/EvmHost.sol (L564-575)
```text
    /**
     * @dev Updates the HostParams. Only callable by cross-chain governance
     * via the configured `hostManager`. The admin has no privileges here —
     * environments that need a privileged admin override (testnets, forks)
     * should use `TestnetHost`, which extends this contract.
     *
     * Marked `virtual` so subclasses can broaden the authorization
     * @param params, the new host params.
     */
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

**File:** evm/src/core/EvmHost.sol (L581-645)
```text
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
        _hostParams.uniswapV2 = params.uniswapV2;
        _hostParams.unStakingPeriod = params.unStakingPeriod;
        _hostParams.challengePeriod = params.challengePeriod;
        _hostParams.consensusClient = params.consensusClient;
        _hostParams.stateMachines = params.stateMachines;
        _hostParams.hyperbridge = params.hyperbridge;

        // add whitelisted state machines
        for (uint256 i = 0; i < stateMachinesLen; ++i) {
            // create if it doesn't already exist
            if (_latestStateMachineHeight[params.stateMachines[i]] == 0) {
                _latestStateMachineHeight[params.stateMachines[i]] = 1;
            }
        }
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

**File:** docs/outbound-request-incentivization.md (L17-17)
```markdown
Not every pallet on hyperbridge that dispatches a request is in scope. `pallet_ismp::child_trie::RequestCommitments` ends up holding commitments for every successful dispatch via `IsmpDispatcher`, which includes both the system messages we want to incentivize (host-executive, intents-coprocessor, token-governor, the relayer pallet's withdrawal path, future modules like bandwidth) and any other pallet that ends up dispatching from hyperbridge. The reward storage is therefore keyed by `source_module_id` and only modules with a non-zero reward are eligible. The `module_id` is the `from` field on the `PostRequest`, which each pallet sets to its unique module identifier. A module with zero reward is treated as not on the allowlist and rejected before any state proof verification runs.
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L92-146)
```rust
	fn dispatch_request(
		&self,
		request: DispatchRequest,
		fee: FeeMetadata<T>,
	) -> Result<H256, anyhow::Error> {
		// collect payment for the request
		if fee.fee != Zero::zero() {
			T::Currency::transfer(
				&fee.payer,
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| IsmpError::Custom(format!("Error withdrawing request fees: {err:?}")))?;
		}

		let request = match request {
			DispatchRequest::Get(dispatch_get) => {
				let get = GetRequest {
					source: self.host_state_machine(),
					dest: dispatch_get.dest,
					nonce: self.next_nonce(),
					from: dispatch_get.from,
					keys: dispatch_get.keys,
					height: dispatch_get.height,
					context: dispatch_get.context,
					timeout_timestamp: if dispatch_get.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_get.timeout)
					},
				};
				Request::Get(get)
			},
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

**File:** modules/pallets/demo/src/lib.rs (L216-227)
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
```
