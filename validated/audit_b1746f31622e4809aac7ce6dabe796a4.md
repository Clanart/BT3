### Title
Live global `challengePeriod` re-read at message-handling time lets a governance parameter update retroactively weaken already-committed state machine heights - ([File: evm/src/core/HandlerV2.sol])

### Summary
The report's core defect is that a mutable, globally-updatable parameter (`_gracePeriod`) is re-read at execution time instead of being fixed/snapshotted onto the action when it was created, so a later parameter change silently changes the security guarantee applied to already-pending actions. Hyperbridge's EVM host has the exact same pattern with `challengePeriod`: it is stored once as a single global `HostParams.challengePeriod` value and is re-read live every time a `StateCommitment` (already stored at some earlier height/time) is used to process requests, responses, or timeouts, instead of being fixed to the value that was in effect when that particular state commitment was stored.

### Finding Description
`updateHostParams` lets `_hostParams.challengePeriod` be overwritten wholesale: [1](#0-0) 

This function is only reachable through `HostManager.onAccept`, which decodes a cross-chain `SetHostParam` message originating from the Hyperbridge parachain and calls `updateHostParams`: [2](#0-1) 

Every handler that consumes a previously-stored `StateCommitment` — `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts` — re-reads the *current* `host.challengePeriod()` and compares it against the elapsed time since that height's commitment was stored, rather than using the challenge period that was configured/expected when the commitment was originally created: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

Nothing in `StateCommitment` or the intermediate-state storage records the challenge period that was active at the time the state was committed: [7](#0-6) 

Because `challengePeriod` is a single global value (not per-height, not per-consensus-update), any legitimate parameter change (e.g. shortening the challenge period for operational reasons) instantly and retroactively reduces — or removes — the security window for *every already-stored, still-pending* `StateCommitment`. A commitment that was stored expecting a long challenge/fraud-proof window can immediately satisfy a newly-shortened window, allowing `handlePostRequests`/`handleGetResponses`/timeout handlers to accept and dispatch requests/timeouts based on that commitment far earlier than the protocol's fraud-proof design intended — before fishermen/relayers had the originally-promised time to detect and challenge a byzantine state update.

The Substrate side shows the identical defect: `challenge_period(id)` is a single mutable value per `StateMachineId`, updatable via the admin extrinsic `update_consensus_state`, and `verify_delay_passed` always uses the live stored value against `state_machine_update_time`, not a value pinned to the specific commitment: [8](#0-7) [9](#0-8) 

### Impact Explanation
This directly threatens the "false proof/state acceptance" invariant the bounty protects: consensus proofs and state commitments must never let a byzantine remote state be trusted before its designated challenge window elapses. Since the challenge period is a single mutable knob applied retroactively to every already-pending commitment, once it is lowered (for any reason, not necessarily malicious), an attacker/relayer can immediately push through `handlePostRequests`, `handleGetResponses`, or the timeout handlers using state commitments that were finalized under the expectation of a longer scrutiny window — enabling processing of requests/timeouts derived from state that fishermen have not yet had the originally-promised time to challenge. This can lead to fund loss or unauthorized cross-chain execution if a byzantine/eclipsed consensus update was submitted just before the parameter change.

### Likelihood Explanation
The trigger event (a `SetHostParam` update lowering `challengePeriod`) is a normal, expected host-management operation performed via legitimate cross-chain governance messages, not requiring a compromised admin key or malicious relayer collusion. Any pending `StateCommitment` at the time of the update is affected automatically — no attacker action is needed to weaken the guard, only to submit the now-prematurely-eligible message once the window is shortened. Given the design is global rather than per-commitment, this occurs by construction any time the operator legitimately tunes the challenge period, making this readily reachable in production operation.

### Recommendation
Snapshot the effective `challengePeriod` (and `unStakingPeriod` if relevant) onto the `StateCommitment`/intermediate state at the moment it is stored (in `handleConsensus`/`storeStateMachineCommitment`), and have `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts` compare against that pinned value instead of the live `host.challengePeriod()`. Apply the analogous fix on the Substrate side by persisting the challenge period alongside `StateCommitmentHeight` rather than relying solely on the mutable `ChallengePeriod` storage map at verification time.

### Proof of Concept
1. Hyperbridge (via legitimate governance) submits a consensus update for state machine `S` at height `H`; `EvmHost` stores `StateCommitment` for `H` and records `stateMachineCommitmentUpdateTime(H) = T0`. The currently configured `challengePeriod` is, say, 1 hour.
2. Before 1 hour elapses, an operator/governance action legitimately submits a `SetHostParam` request (via `HostManager.onAccept` → `EvmHost.updateHostParams`) reducing `challengePeriod` to `0` or a much smaller value (e.g., for a new/expedited state machine or an operational fix unrelated to `H`).
3. An unprivileged relayer immediately calls `handlePostRequests` (or `handleGetResponses`/timeout handlers) with a proof against height `H`. The check `challengePeriod != 0 && challengePeriod > delay` now trivially passes because `challengePeriod` was read live and is now small/zero, even though `H`'s commitment was created under the expectation of the original 1-hour window.
4. Requests/timeouts tied to `H` are dispatched to destination modules well before the originally-intended fraud-proof/challenge window elapsed, exactly mirroring the "expired/under-protected action executed early due to a live parameter re-read" pattern from the source report.

### Citations

**File:** evm/src/core/EvmHost.sol (L623-636)
```text
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

**File:** evm/src/core/HandlerV2.sol (L152-164)
```text
        if (keccak256(previousState) == keccak256(verifiedState)) return;
        host.storeConsensusState(verifiedState);

        uint256 intermediatesLen = intermediates.length;
        for (uint256 i = 0; i < intermediatesLen; i++) {
            IntermediateState memory intermediate = intermediates[i];
            uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
            if (latestHeight != 0 && intermediate.height > latestHeight) {
                StateMachineHeight memory stateMachineHeight =
                    StateMachineHeight({stateMachineId: intermediate.stateMachineId, height: intermediate.height});
                host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
            }
        }
```

**File:** evm/src/core/HandlerV2.sol (L181-185)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** evm/src/core/HandlerV2.sol (L217-221)
```text
    function handleGetResponses(IHost host, GetResponseMessage calldata message) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(message.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** evm/src/core/HandlerV2.sol (L254-260)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** evm/src/core/HandlerV2.sol (L293-296)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** modules/ismp/core/src/handlers.rs (L104-114)
```rust
pub fn verify_delay_passed<H>(host: &H, proof_height: &StateMachineHeight) -> Result<bool, Error>
where
	H: IsmpHost,
{
	let update_time = host.state_machine_update_time(*proof_height)?;
	let delay_period = host
		.challenge_period(proof_height.id)
		.ok_or(Error::ChallengePeriodNotConfigured { state_machine: proof_height.id })?;
	let current_timestamp = host.timestamp();
	Ok(delay_period.as_secs() == 0 || current_timestamp.saturating_sub(update_time) > delay_period)
}
```

**File:** modules/pallets/ismp/src/lib.rs (L410-437)
```rust
		/// Modify the unbonding period and challenge period for a consensus state.
		/// The dispatch origin for this call must be `T::AdminOrigin`.
		///
		/// - `message`: `UpdateConsensusState` struct.
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().writes(2))]
		#[pallet::call_index(3)]
		pub fn update_consensus_state(
			origin: OriginFor<T>,
			message: UpdateConsensusState,
		) -> DispatchResult {
			T::AdminOrigin::ensure_origin(origin)?;

			let host = Pallet::<T>::default();

			if let Some(unbonding_period) = message.unbonding_period {
				host.store_unbonding_period(message.consensus_state_id, unbonding_period)
					.map_err(|_| Error::<T>::UnbondingPeriodUpdateFailed)?;
			}

			for (state_id, period) in message.challenge_periods {
				let id =
					StateMachineId { state_id, consensus_state_id: message.consensus_state_id };
				host.store_challenge_period(id, period)
					.map_err(|_| Error::<T>::UnbondingPeriodUpdateFailed)?;
			}

			Ok(())
		}
```
