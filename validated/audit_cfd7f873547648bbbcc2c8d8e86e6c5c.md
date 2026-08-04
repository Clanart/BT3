# Analysis Result

## Title
`HostManager.onAccept` authenticates only the source **chain**, never the sending **module** — allowing any Hyperbridge-side sender to trigger privileged withdraw/host-param actions - (File: `evm/src/core/HostManager.sol`)

## Summary
The external report's core broken invariant is: a privileged action is gated by an access check performed at one point (`DepositHook`, checking allowed-list ∪ NFT ownership) but a *different, weaker* check is performed at the point where the corresponding privileged action is actually executed (`RedeemHook`, checking only the allowed-list). The mismatch between the two gates lets a legitimately-onboarded actor pass the first gate but fail/bypass intent at the second, causing loss of funds. The same class of bug — binding only part of the required identity at the enforcement point instead of the full identity that was established upstream — exists in Hyperbridge's cross-chain governance relay between the `intents-coprocessor` pallet (Hyperbridge chain) and `HostManager` (EVM destination chain).

## Finding Description
On the Hyperbridge (Substrate) side, the `intents-coprocessor` pallet dispatches governance instructions (host-param updates, bridge-revenue withdrawals, paymaster fund sweeps) to EVM chains by constructing a `DispatchPost` whose `from` field is deliberately set to the pallet's own module identifier, `PALLET_INTENTS_ID`: [1](#0-0) 

That `from` value is the intended, meaningful module-level identity binding that the destination is supposed to authenticate against (analogous to prePO's NFT/allow-list membership check at deposit time).

On the EVM side, `HostManager.onAccept` is the single enforcement point that executes these governance instructions (`Withdraw` and `SetHostParam`): [2](#0-1) 

The only authentication performed here is:
```solidity
if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
This checks that `request.source` equals the **chain id** of Hyperbridge — but it never checks `request.from`, i.e. it never verifies that the request actually originated from the `intents-coprocessor` pallet (or any specific authorized module on that chain). This directly contradicts the Hyperbridge Pivot requirement that request paths "must bind chain id, module/app identity, commitment uniqueness, and one-time receipt handling" — only the chain-id half of that binding is enforced here.

Dispatch itself is a low-level, generic capability: any pallet on the Hyperbridge runtime with access to `T::Dispatcher`/`IsmpDispatcher` can freely set `from`, `to`, and `body` when calling `pallet_ismp::Pallet::<T>::dispatch_request`: [3](#0-2) 

Nothing in this low-level dispatcher constrains `from` to the calling pallet's true identity — it is trusted, unenforced input. Combined with `HostManager`'s failure to check `request.from`, the entire authorization for both fund withdrawal and host-parameter mutation rests solely on "did this PostRequest originate from the Hyperbridge state machine," which is exactly the kind of narrower/incomplete re-check that caused the prePO loss-of-funds bug (the second gate silently trusts less than the first gate established).

The privileged actions reachable through this single, under-checked gate include: [4](#0-3) [5](#0-4) 

## Impact Explanation
`HostManager.onAccept` gates two governance-critical, fund-moving/host-mutating entry points: `withdraw()` (moves the entire accrued bridge-revenue/fee-token balance of `EvmHost` to an arbitrary `beneficiary`) and `updateHostParams()` (can rewrite `hostManager`, `handler`, `consensusClient`, `feeToken`, and the trusted state-machine set — i.e., who is trusted to certify state going forward). Because the enforcement point checks only the source chain id and not the sending module identity, this collapses the intended module-scoped trust boundary (only `intents-coprocessor` should be able to trigger these actions) down to a chain-scoped one (anything dispatched from Hyperbridge with `to = HostManager`). This matches the Impact Gate's "stealing or loss of funds," "unauthorized transaction or execution," and "cross-chain admin/host-management effects... reachable through... wrong module bindings" categories directly.

## Likelihood Explanation
This is a structural/code-level gap rather than a race condition, so it is deterministically present in every deployment of `HostManager`/`EvmHost` as written — the missing check is unconditional, and nothing else in the call path (the low-level `pallet_ismp` dispatcher) re-establishes the module binding that `HostManager` fails to verify. I was not able to fully enumerate, within the available tool budget, every pallet in the current runtime configuration that has access to the generic `IsmpDispatcher::dispatch_request` capability with a user- or attacker-influenceable `to`/`body`, so I cannot state with certainty today's exact minimal-privilege trigger path (e.g., whether some non-governance pallet's extrinsic lets an ordinary signed account indirectly steer `to`/`body` toward `HostManager`). That specific gap in verification should be treated as the primary open question for a background engineer to resolve before/alongside the fix.

## Recommendation
In `HostManager.onAccept`, add an explicit check that `request.from` equals the expected `intents-coprocessor` pallet module id (configurable per-deployment, analogous to how `_params.host` is pinned), in addition to the existing `request.source` chain check — mirroring how `DepositHook`'s full access check should have been re-applied verbatim in `RedeemHook` rather than substituted with a narrower one. Store the authorized module id in `HostManagerParams` and reject any `onAccept` call whose `request.from` does not match it.

## Proof of Concept
Conceptual PoC (cannot be fully executed without deploying the pallet/contract pair, but demonstrates the missing check):
1. On Hyperbridge, dispatch a `PostRequest` via any pallet capable of calling `pallet_ismp::Pallet::<T>::dispatch_request` (per `modules/pallets/ismp/src/dispatcher.rs:92-151`), setting:
   - `source = Hyperbridge` (forced automatically by the dispatcher),
   - `from = <any bytes, e.g. an unrelated pallet id>` (not `PALLET_INTENTS_ID`),
   - `to = <HostManager address on target EVM chain>`,
   - `body = 0x00 || abi.encode(WithdrawParams{beneficiary: attacker, amount: full_balance, token: feeToken})`.
2. Once relayed and delivered on the EVM chain, `EvmHost.dispatchIncoming` routes the request to `HostManager.onAccept` (`evm/src/core/HostManager.sol:95`).
3. `onAccept` checks only `request.source.equals(hyperbridge())` — true, since it did originate from Hyperbridge — and proceeds to decode `OnAcceptActions.Withdraw` and call `IHostManager(_params.host).withdraw(withdrawParams)` (`evm/src/core/EvmHost.sol:651-660`), transferring the full bridge-revenue balance to `attacker`, without ever having verified that the message came from the `intents-coprocessor` pallet specifically.

### Citations

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L924-936)
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

**File:** evm/src/core/EvmHost.sol (L573-576)
```text
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
