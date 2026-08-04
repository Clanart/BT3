### Title
Permissionless out-of-order delivery of governance `UpdateParams`/`HostParams` ISMP messages allows rollback to stale protocol configuration — ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
This is a direct analog of the ECO Protocol `rebase` bug: a value that is meant to always move forward (the "current" protocol configuration) is propagated cross-chain via independent, permissionless ISMP messages with no monotonicity/versioning check on the receiving side. Because ISMP delivery order is controlled entirely by whoever submits the proof (any address, not just a "trusted" relayer), an old-but-authentic governance message can be delivered *after* a newer one, silently reverting live contract state to stale values.

### Finding Description
Hyperbridge governance updates on-chain app parameters (`IntentGatewayV2`/`IntentsBase`, `SimplexPaymaster`, `EvmHost` via `HostManager`) by dispatching an ISMP `PostRequest` whose body is decoded and applied wholesale in `onAccept`: [1](#0-0) 

`_updateParams` replaces `_params` (host, dispatcher, price oracle, fee bps, etc.) unconditionally — there is no sequence number, height, or timestamp comparison against the currently stored params. The same unconditional-replace pattern is used by `SimplexPaymaster`'s `UpdateParams` request kind (`Params` struct holds pricing/treasury/oracle config, replaced "wholesale, no merge semantics" per the pallet's own doc comment) and by `EvmHost.updateHostParams`/`HostManager`. [2](#0-1) 

On the dispatch side, `pallet-host-executive` (and the intents-coprocessor pallet analogously for `IntentGatewayParams`/`PaymasterParams`) sends each parameter update as an independent `DispatchPost` with `timeout: 0`: [3](#0-2) 

`timeout: 0` is documented elsewhere in this codebase as meaning the request never expires (e.g. the LayerZero endpoint adapter dispatches with `timeout: 0` specifically because "LZ messages don't have a timeout concept... messages are retried, not expired"). That means two consecutive governance param updates, A (older) and B (newer, corrective), both remain deliverable indefinitely.

Delivery of a POST request to the destination app is permissionless — `HandlerV2.handlePostRequests` can be called by any address holding a valid membership proof for a state commitment, and `EvmHost.dispatchIncoming` records `_msgSender()` as the delivering relayer with no restriction on who that is or in what order they submit distinct requests: [4](#0-3) 

The only replay protection is per-request-commitment (`_requestReceipts[commitment]`), which prevents the *same* request from being delivered twice — it does nothing to prevent a *different, older* request (A) from being delivered *after* a newer request (B). This mirrors exactly the ECO `rebase` flaw: the guard against replay is receipt/commitment-based, not value/order-based, so a stale-but-valid message queued earlier can still land later and overwrite newer state.

### Impact Explanation
An attacker (any address able to submit the proof — no privileged role required) can withhold delivery of an old governance `UpdateParams`/`PaymasterParams`/`HostParams` message and later deliver it after a corrective update has landed, rolling the destination contract back to stale parameters: e.g. a previously-larger `maxOracleAge` (weakening the paymaster's stale-price protection — see `SimplexPaymaster.testUpdateParamsTightensOracleAge`, which shows governance actively tightens this value over time), a previously-lower `protocolFeeBps`/`destinationFeeBps`, a stale/decommissioned `priceOracle`, or a previously-looser `challengePeriod`/`consensusClient`/`hostManager` combination on `EvmHost`. Since these are core custody/pricing/oracle-staleness parameters, reverting them can enable mispriced paymaster gas charges, incorrect intent-settlement fee splits, or acceptance of stale oracle data — i.e. fund loss / wrong-amount settlement, matching the bounty's "logic attacks" / "false state acceptance" categories.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: governance only needs to issue two updates in succession (a routine correction, a security tightening, or a two-step migration) for the window to exist, and since `timeout: 0` means the earlier message never expires, the attack window is unbounded. No relayer collusion, prover compromise, or admin key is needed — delivering an already-signed, already-dispatched ISMP message is exactly what the permissionless `handlePostRequests`/`onAccept` path is designed to allow anyone to do.

### Recommendation
Add a monotonically increasing version/nonce (or the dispatching block height/timestamp) to every parameter struct (`Params`, `PaymasterParams`, `HostParams`) and have `_updateParams`/`onAccept`/`updateHostParamsInternal` reject any incoming update whose version is not strictly greater than the currently stored version — mirroring the ECO fix of binding the message to a source block number and rejecting non-increasing values on the receiving side.

### Proof of Concept
1. Governance dispatches `UpdateParams(A)` at t0 (e.g., `maxOracleAge = 1 day`), producing ISMP `PostRequest` R_A with `timeout = 0`.
2. Shortly after, governance dispatches `UpdateParams(B)` at t1 (e.g., tightened `maxOracleAge = 50s`), producing `PostRequest` R_B, also `timeout = 0`.
3. A relayer (anyone) delivers R_B first via `HandlerV2.handlePostRequests` → `SimplexPaymaster.onAccept` applies B; contract now uses the tightened, correct `maxOracleAge`.
4. The same or any other address later delivers R_A (never expired, distinct commitment from R_B so not rejected as duplicate) → `onAccept` unconditionally overwrites `_params`/`Params` back to A's stale, looser `maxOracleAge`/oracle/fee configuration.
5. The paymaster/gateway now silently operates under the reverted, weaker configuration until governance notices and re-issues another corrective update — during which stale-oracle-price or mispriced-fee exploitation is possible, exactly analogous to the ECO attacker replaying an old rebase message to desynchronize L1/L2 balances.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L541-568)
```text
     * @dev Updates the gateway's configuration parameters and per-destination protocol fees.
     * Called by Hyperbridge governance to modify fee settings, host address, dispatcher,
     * price oracle, and other operational parameters.
     *
     * Validates all params before applying. Emits ParamsUpdated with the old and new params,
     * then iterates over any destination-specific fee overrides and applies them to
     * `_destinationProtocolFees`.
     *
     * @param update The parameter update containing new params and destination fee overrides.
     */
    function _updateParams(ParamsUpdate memory update) internal {
        _validateParams(update.params);

        emit ParamsUpdated({previous: _params, current: update.params});
        _params = update.params;

        for (uint256 i; i < update.destinationFees.length;) {
            bytes memory chain = update.destinationFees[i].chain;
            uint256 feeBps = update.destinationFees[i].destinationFeeBps;
            if (feeBps >= 10_000) revert InvalidInput();
            _destinationProtocolFees[keccak256(chain)] = feeBps;

            unchecked {
                ++i;
            }
            emit DestinationProtocolFeeUpdated(string(chain), feeBps);
        }
    }
```

**File:** modules/pallets/intents-coprocessor/src/types.rs (L55-70)
```rust
/// Pricing and treasury parameters for a SimplexPaymaster instance. Mirrors the
/// `Params` struct in `SimplexPaymaster.sol`; the contract replaces these wholesale
/// on `UpdateParams` (no merge semantics).
#[derive(Clone, Debug, Encode, Decode, DecodeWithMemTracking, TypeInfo, PartialEq, Eq)]
pub struct PaymasterParams {
	/// Native asset / USD Chainlink feed
	pub native_oracle: H160,
	/// Markup in basis points applied on top of the oracle price (10000 = 100%)
	pub markup_bps: U256,
	/// Receives markup surplus and EntryPoint deposit withdrawals
	pub treasury: H160,
	/// Maximum Chainlink oracle staleness, in seconds
	pub max_oracle_age: U256,
	/// Slippage tolerance in basis points for fee-recycling swaps
	pub swap_slippage_bps: U256,
}
```

**File:** modules/pallets/host-executive/src/lib.rs (L182-217)
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
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```
