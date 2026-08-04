This confirms a real, provable analog. `dispatch_request` on `pallet-ismp` — the low-level dispatcher used by every pallet, including custom/example pallets — accepts an arbitrary caller-supplied `from: Vec<u8>` module id with **no validation that the caller is authorized to use that module id**, and this `from` field is embedded directly into the resulting `PostRequest` that crosses the bridge. On the EVM receiving side, `HostManager.onAccept` (and `BandwidthManager.onAccept`) only check `request.source.equals(hyperbridge)` — i.e., that the message came from the correct **chain** — but never check `request.from` against the expected origin module (e.g. `PALLET_ID` = `b"hostexec"`). This is a wrong-module-binding gap matching the "Cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow" pivot.

### Title
Unauthenticated cross-chain module binding lets any Substrate pallet/extrinsic spoof `pallet-ismp-host-executive` and hijack `HostManager.updateHostParams`/`withdraw` on EVM - (File: evm/src/core/HostManager.sol)

### Summary
`HostManager.onAccept` (and the analogous `BandwidthManager.onAccept`) validate only `request.source` (the origin *chain*), never `request.from` (the origin *module/pallet* on that chain). Meanwhile `pallet_ismp::Pallet::dispatch_request` accepts a caller-supplied `DispatchPost.from: Vec<u8>` with no check that the calling extrinsic/pallet actually owns that module id. Any signed extrinsic that can reach `IsmpDispatcher::dispatch_request` (e.g. a generic "send_message"/"demo" pallet, or any future pallet wired to the dispatcher) can therefore forge `from = PALLET_ID::Pallet(b"hostexec")` and send an ISMP POST to the `HostManager` contract address, which will accept it as legitimate governance.

### Finding Description
- `evm/src/core/HostManager.sol:95-109`: `onAccept` checks `restrict(_params.host)` (caller is the local EvmHost — always true for any inbound message) and `request.source.equals(hyperbridge)` (message chain), then unconditionally decodes `request.body[0]` as `OnAcceptActions.Withdraw` or `SetHostParam` and calls `IHostManager(host).withdraw(...)` / `updateHostParams(...)` — full governance authority.
- There is no check of `request.from` against `PALLET_ID` (`b"hostexec"`, defined in `modules/pallets/host-executive/src/lib.rs:52`), the only module meant to originate these governance messages.
- On the Substrate side, `modules/pallets/ismp/src/dispatcher.rs:92-151` (`Pallet::dispatch_request`) builds the outgoing `PostRequest` directly from the caller-supplied `DispatchPost{ from, to, body, .. }` without verifying that the dispatching pallet/extrinsic is authorized to use that particular `from` id. `docs/content/developers/polkadot/dispatching.mdx` and `modules/pallets/demo/src/lib.rs` show this is a generic, low-level API any pallet is expected to call with a self-chosen `from`.
- Consequently, an attacker-controlled (or simply careless/duplicate) module on the Hyperbridge state machine can set `from = PALLET_ID.to_bytes()` (host-executive's id) and `to = <HostManager address>`, craft `body = [SetHostParam byte] || abi.encode(HostParams{...})`, and dispatch it. Once relayed and proven (a normal, permissionless relay/proof step — no malicious relayer or prover required, since the message itself is validly signed by real consensus/state proofs, it's just from the wrong logical module), `HostManager.onAccept` will accept it as if it came from `pallet-ismp-host-executive`, and call `updateHostParams` to replace `hostManager`, `handler`, `consensusClient`, `admin`, etc. It can equally decode `Withdraw` to drain `EvmHost`'s revenue to an attacker beneficiary via `withdraw(WithdrawParams)` in `evm/src/core/EvmHost.sol:651-660`.
- The corrupted value is `HostParams.hostManager`/`HostParams.consensusClient`/`HostParams.admin` (full host takeover) or `WithdrawParams.beneficiary`/`amount` (direct fund theft) — accepted because `onAccept`'s only guard, `request.source`, is chain-level, not module-level.

### Impact Explanation
This directly hits the bounty's fund-theft and unauthorized-execution categories: an attacker can (a) redirect `EvmHost.withdraw` to steal all accumulated protocol/relayer fees held by `EvmHost`, and (b) rewrite `HostParams` to point `consensusClient`/`handler`/`hostManager` to attacker-controlled contracts, enabling subsequent false-state acceptance and full compromise of the bridge on that EVM chain — a complete loss-of-funds and unauthorized-execution primitive, not merely a compromised-relayer or governance-actor scenario, since the flaw is the missing module-identity check itself.

### Likelihood Explanation
Likelihood depends on whether any deployed, non-privileged pallet on the live Hyperbridge/Nexus/Gargantua runtime allows a signed user extrinsic to call `IsmpDispatcher::dispatch_request` with an attacker-chosen `from`. The demo/example pallets in this repo show the pattern is common and expected (`from: PALLET_ID.to_bytes()` hardcoded per-pallet, not enforced by `pallet-ismp` itself). If the production runtime restricts `dispatch_request` callers/origins tightly (e.g., only root-gated pallets ever call it with a fixed `from`), exploitation requires a mis-configured or vulnerable intermediary pallet; I could not fully confirm from the indexed files whether any currently-deployed non-privileged runtime pallet lets a normal user pick or influence `from` freely versus always hardcoding a distinct, unprivileged module id. This uncertainty should be resolved by checking the production runtime's pallet configs (`parachain/runtimes/gargantua/src/ismp.rs`, `parachain/runtimes/nexus/src/ismp.rs`) for any user-facing extrinsic that can set/influence `from` to `b"hostexec"` or another privileged id.

### Recommendation
Add module-identity binding checks in every EVM `onAccept` handler (`HostManager.sol`, `BandwidthManager.sol`, and any other privileged app), verifying `request.from` equals the expected pallet id (`b"hostexec"`, `b"BWMARKET"`, etc.), not just `request.source`. Symmetrically, harden `pallet_ismp::dispatch_request` (or wrap it) so that `from` is derived from the calling pallet's own fixed identifier rather than being freely supplied by the caller, preventing any pallet from spoofing another pallet's module id.

### Proof of Concept
1. Identify/deploy a pallet `X` on the Hyperbridge state machine that calls `T::IsmpDispatcher::dispatch_request` with a `DispatchPost` whose `from` field it controls (pattern shown in `modules/pallets/demo/src/lib.rs:216-239` and `docs/content/developers/polkadot/dispatching.mdx:57-77`).
2. From pallet `X`'s extrinsic, submit `DispatchPost{ dest: StateMachine::Evm(<chain>), from: b"hostexec".to_vec(), to: <HostManager address bytes>, timeout: 0, body: [1u8 /* SetHostParam */].chain(abi_encode(HostParams{ hostManager: attacker, admin: attacker, ... })) }`.
3. Once the request is relayed and proven through the normal ISMP handler pipeline (no malicious relayer/prover needed — proof validity is about chain state, not module identity), `HostManager.onAccept` at `evm/src/core/HostManager.sol:95-108` receives it, passes the `request.source.equals(hyperbridge)` check, and calls `EvmHost.updateHostParams(hostParams)`, replacing the legitimate `hostManager`/`admin`/`consensusClient` with attacker-controlled addresses.
4. Attacker now calls `EvmHost.updateHostParams` (as the new `hostManager`) or `EvmHost.withdraw` directly to drain fees to any `beneficiary`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** modules/pallets/host-executive/src/lib.rs (L44-53)
```rust
	use ismp::{
		dispatcher::{DispatchPost, DispatchRequest, FeeMetadata, IsmpDispatcher},
		host::StateMachine,
	};
	use pallet_ismp::ModuleId;
	use primitive_types::{H160, U256};

	/// ISMP module identifier
	pub const PALLET_ID: ModuleId = ModuleId::Pallet(PalletId(*b"hostexec"));

```

**File:** modules/pallets/ismp/src/dispatcher.rs (L92-151)
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

		let commitment = Pallet::<T>::dispatch_request(request, fee)?;

		Ok(commitment)
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

**File:** evm/src/apps/BandwidthManager.sol (L196-225)
```text
    /// @notice Inbound governance from `pallet-bandwidth`. The first
    /// body byte selects `OnAcceptActions`; the remainder is the
    /// action's ABI-encoded payload.
    /// @dev Only the configured host may invoke (`onlyHost`); the
    /// request's `source` must additionally equal hyperbridge.
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        PostRequest calldata request = incoming.request;

        if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.SetTiers) {
            Tier[] memory updates = abi.decode(request.body[1:], (Tier[]));
            for (uint256 i = 0; i < updates.length; i++) {
                tierPrice[updates[i].tier] = updates[i].price;
                emit TierSet(updates[i].tier, updates[i].price);
            }
        } else if (action == OnAcceptActions.Withdraw) {
            Withdrawal memory w = abi.decode(request.body[1:], (Withdrawal));
            if (w.token != address(0)) {
                IERC20(w.token).safeTransfer(w.beneficiary, w.amount);
            } else {
                (bool sent,) = w.beneficiary.call{value: w.amount}("");
                if (!sent) revert InsufficientNativeToken();
            }
            emit Withdrawn(w.token, w.beneficiary, w.amount);
        } else {
            revert UnauthorizedAction();
        }
    }
```
