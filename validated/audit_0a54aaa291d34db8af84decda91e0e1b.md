This confirms the vulnerability path. Any pallet on Hyperbridge — including example/demo pallets that let a **signed, unprivileged user** pick an arbitrary `to` and `body` for a `DispatchPost` (e.g. `pallet_ismp_demo::dispatch_to_evm`, or any other pallet built with the documented `IsmpDispatcher::dispatch_request` pattern) — can set `to = <victim EvmHost's HostManager address>` and `body = [0] ++ abi.encode(WithdrawParams{beneficiary: attacker, amount, token})`. `pallet_ismp::Pallet::dispatch_request` (`modules/pallets/ismp/src/impls.rs:90-121` and `modules/pallets/ismp/src/dispatcher.rs:92-151`) only sets `request.source = self.host_state_machine()` — it never validates or restricts `to`/`body`/`from` against any allowlist. On the EVM side, `HostManager.onAccept()` (`evm/src/core/HostManager.sol:95-109`) only checks `request.source.equals(hyperbridge())`, i.e. that the message came from the Hyperbridge state machine at all — it never checks `request.from` against `pallet_ismp_relayer::MODULE_ID` (`b"HYPR-FEE"`-adjacent module id used by the real withdrawal flow, see `modules/pallets/ismp/src/dispatcher.rs:38-41`). Since *every* successful dispatch from Hyperbridge carries `source == hyperbridge` by construction, this check is a tautology and provides no authentication of the sending module.

### Title
Unauthenticated `from` binding in `HostManager.onAccept` lets any Hyperbridge module drain bridge revenue - (File: evm/src/core/HostManager.sol)

### Summary
`HostManager.onAccept` authorizes cross-chain `Withdraw`/`SetHostParam` actions solely by checking `request.source == hyperbridge()`. It never checks `request.from` (the originating module identifier on Hyperbridge). Because `request.source` is set to the Hyperbridge state machine identifier for *every* request dispatched from Hyperbridge regardless of which pallet or account issued it, this check authenticates nothing about the caller's privilege level.

### Finding Description
The legitimate relayer fee withdrawal flow dispatches a `DispatchPost{ from: MODULE_ID (relayer pallet), to: host_manager_address, body: WithdrawParams{...} }` via `pallet-relayer`'s `withdraw()` (`modules/pallets/relayer/src/withdrawal.rs:134-159`), and `pallet_ismp::Pallet::dispatch_request` (`modules/pallets/ismp/src/impls.rs:90-121`) accepts and commits it with no restriction on `to`, `from`, or `body` content beyond commitment-uniqueness. Any other pallet on the Hyperbridge runtime that exposes a generic dispatch entrypoint to signed, unprivileged users — the documented pattern in `docs/content/developers/polkadot/dispatching.mdx:57-77` and mirrored concretely in `modules/pallets/demo/src/lib.rs:216-239` (`dispatch_to_evm`) — lets a caller freely choose `to` (any EVM address, including a target chain's `HostManager`) and `body` (arbitrary bytes).

On the EVM destination, `EvmHost.dispatchIncoming` (`evm/src/core/EvmHost.sol:794-818`) forwards the request to whatever contract `request.to` resolves to, calling `onAccept`. `HostManager.onAccept` (`evm/src/core/HostManager.sol:95-109`) is `restrict(_params.host)`-gated for the caller (must be the local `EvmHost`), which is fine, but its *content* authorization is only `request.source.equals(IHost(_params.host).hyperbridge())`. Since `dispatchIncoming` only ever calls modules with requests whose `source` is already verified as originating from the connected Hyperbridge chain (that is inherent to the ISMP delivery pipeline itself, not module-specific), this check does not distinguish "the trusted relayer pallet sent this" from "any account on Hyperbridge dispatched an arbitrary POST to my address." An attacker with a Hyperbridge account and access to any permissionless dispatch entrypoint (e.g., the demo pallet, or any future intent/app pallet that forwards user-supplied `to`/`body`) can craft `body = abi.encodePacked(uint8(0), abi.encode(WithdrawParams({beneficiary: attacker, amount: hostBalance, token: feeToken})))` targeting the victim EVM host's `HostManager`, causing `IHostManager(host).withdraw(...)` (`evm/src/core/EvmHost.sol:651-660`) to transfer the entire accumulated bridge revenue to the attacker.

### Impact Explanation
This is unauthorized transaction execution / fund theft against protocol-owned "bridge revenue" (accumulated relayer/protocol fees held by `EvmHost`), reachable by an unprivileged Hyperbridge account with no compromised relayer, prover, or admin key required — matching the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories, and specifically the pivot: "Cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow."

### Likelihood Explanation
Exploitability depends on whether any pallet deployed in the actual Hyperbridge runtime configuration exposes a `dispatch_request`/`send_message`-style extrinsic to ordinary signed accounts with attacker-controlled `to`/`body` (as documented and exemplified by `pallet-example`/`pallet-ismp-demo`). If the production runtime only wires privileged/system pallets (relayer, host-executive, intents-coprocessor, token-governor) into `IsmpDispatcher` and never exposes a raw pass-through to end users, the path is not reachable. I could not confirm from the indexed code whether the production runtime includes such a permissionless pass-through pallet; the demo pallet is explicitly documented as an example/tutorial pattern. This uncertainty should be resolved by checking the actual runtime pallet configuration (`runtime/.../lib.rs` construct_runtime!) for any pallet granting unprivileged accounts a `DispatchPost` with attacker-chosen `to`.

### Recommendation
`HostManager.onAccept` must authenticate `request.from` against the known, single trusted module identifier used by the legitimate withdrawal/governance dispatcher (e.g. `pallet_ismp_relayer::MODULE_ID` for withdrawals and the host-executive module id for `SetHostParam`), not merely `request.source`. This mirrors the fix pattern already used elsewhere in the codebase (e.g. `OutboundRequestDeliveryReward` keyed by `request.from` allowlist in `modules/pallets/relayer/src/outbound_request.rs:143-149`, and `IntentsBase._authenticate` checking `request.from` in `evm/src/apps/intentsv2/ExtrinsicIntents.sol:63-67`). Additionally, audit every pallet wired into `IsmpDispatcher` in the production runtime to ensure none allows an unprivileged signer to set an arbitrary `to`/`body` pair that could target `HostManager` or other privileged EVM modules.

### Proof of Concept
Conceptual sequence (requires confirming a permissionless dispatch pallet exists in the deployed runtime; using `pallet-ismp-demo`'s `dispatch_to_evm` as the closest documented analog):
1. Attacker holds a normal signed account on Hyperbridge.
2. Attacker calls a permissionless dispatch extrinsic (following `modules/pallets/demo/src/lib.rs:216-239` pattern) with `to = <victim chain's HostManager address>` and a custom `body` (the demo pallet itself only sends a fixed `"Hello from polkadot"` body, so this requires a pallet that lets the caller set `body` — the documented generic pattern in `docs/content/developers/polkadot/dispatching.mdx:57-77` does allow this via `send_message(post: DispatchPost, fee)`).
3. `body = [0u8] ++ abi.encode(WithdrawParams{beneficiary: attacker_address, amount: <host balance>, token: feeToken})`.
4. A relayer (any permissionless relayer, no collusion needed) delivers the message; `EvmHost.dispatchIncoming` calls `HostManager.onAccept`.
5. `onAccept` passes its only check (`request.source == hyperbridge()`), decodes the `Withdraw` action, and calls `EvmHost.withdraw`, transferring the full fee-token balance to the attacker. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L134-159)
```rust
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
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
		};
```

**File:** modules/pallets/ismp/src/impls.rs (L90-121)
```rust
	pub fn dispatch_request(request: Request, meta: FeeMetadata<T>) -> Result<H256, ismp::Error> {
		let commitment = hash_request::<Pallet<T>>(&request);

		if RequestCommitments::<T>::contains_key(commitment) {
			Err(ismp::Error::Custom("Duplicate request".to_string()))?
		}

		let (dest_chain, source_chain, nonce) =
			(request.dest_chain(), request.source_chain(), request.nonce());
		let leaf_index_and_pos = T::OffchainDB::push(Leaf::Request(request));
		// Deposit Event
		Pallet::<T>::deposit_event(Event::Request {
			request_nonce: nonce,
			source_chain,
			dest_chain,
			commitment,
		});

		RequestCommitments::<T>::insert(
			commitment,
			RequestMetadata {
				offchain: LeafIndexAndPos {
					leaf_index: leaf_index_and_pos.index,
					pos: leaf_index_and_pos.position,
				},
				fee: meta,
				claimed: false,
			},
		);

		Ok(commitment)
	}
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

**File:** docs/content/developers/polkadot/dispatching.mdx (L57-77)
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
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L143-149)
```rust
		let module_id: BoundedVec<u8, ModuleIdBound> = request
			.from
			.clone()
			.try_into()
			.map_err(|_| Error::<T>::OutboundRequestModuleIdTooLong)?;
		let reward = OutboundRequestDeliveryReward::<T>::get(&module_id);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundRequestNoRewardConfigured);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L61-67)
```text
     * @param request The incoming post request to authenticate.
     */
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```
