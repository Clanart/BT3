## Finding

### Title
`challengePeriod` is read live at message-handling time rather than pinned per state commitment, letting a parameter reduction retroactively finalize already-stored (still-disputable) state - ([File: evm/src/core/HandlerV2.sol])

### Summary
This is a direct local analog of the external report's "Operative Risks tied to changing Risk Based Parameter" class. `EvmHost.updateHostParams` (governance-gated, no malicious actor required) can change `challengePeriod` at any time, with no pause of in-flight message processing, no buffer, and no re-validation of already-stored, not-yet-finalized state commitments. Because `HandlerV2` re-reads the *current* `host.challengePeriod()` on every call instead of using the challenge period that was in effect when the referenced state commitment was stored, a routine reduction of this parameter instantly and retroactively shrinks (or removes) the dispute window for every state commitment already sitting in storage.

### Finding Description
The challenge period exists specifically to give fishermen/relayers time to submit fraud proofs (`vetoStateCommitment`/`_vetoes`, `FraudProof` handling in `modules/ismp/core/src/handlers.rs`) before a state commitment's data is trusted enough to dispatch requests, responses, or timeouts.

In `evm/src/core/HandlerV2.sol`, every entrypoint (`handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts`) computes: [1](#0-0) 
```
uint256 timestamp = block.timestamp;
uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
uint256 challengePeriod = host.challengePeriod();
if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```
`stateMachineCommitmentUpdateTime` is fixed at the moment the commitment was stored, but `challengePeriod` is fetched fresh from current host params — it is never snapshotted alongside the commitment. The same live-read pattern repeats for `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts`. [2](#0-1) [3](#0-2) [4](#0-3) 

`updateHostParams`/`updateHostParamsInternal` in `EvmHost.sol` validates addresses, unstaking period floor, and fee-token balance, but performs **no check or buffer at all on `challengePeriod`** and takes effect immediately for every state machine height already stored: [5](#0-4) 

The Rust-side ISMP core has the identical pattern — `verify_delay_passed` recomputes `delay_period` from `host.challenge_period(...)` at call time against a fixed `update_time`: [6](#0-5) 

### Impact Explanation
This is exactly the report's "risk based parameter" pattern: the setter itself (`updateHostParams`/`update_host_params`) is not the bug, but the mechanism of enacting the change has none of the guards the report calls for — no pause of processing, no buffer/grandfathering for already-stored commitments, and no re-verification of solvency/validity of in-flight state. The moment `challengePeriod` is lowered (a routine, expected governance action — e.g. tuning latency, not an attack), every state commitment already stored under the old (longer) assumption becomes eligible for `handlePostRequests`/`handleGetResponses`/timeout dispatch immediately, even though it was expected to remain challengeable for longer. Any unprivileged relayer can then push a proof for that stale height through `HandlerV2` before the community/fishermen had the originally-promised window to submit a fraud proof and veto it, resulting in **false state acceptance** and dispatch of requests/responses derived from state that should still be in dispute — directly matching the "false proof/state acceptance" bounty category. No malicious relayer, prover, or governance actor is needed; only a normal parameter update plus an ordinary, unprivileged caller of the public `handlePostRequests`/`handleGetResponses`/timeout functions.

### Likelihood Explanation
`challengePeriod` is a documented, expected-to-change operational parameter (unlike `unStakingPeriod`, which has an enforced floor, or `feeToken`, which has a balance-sweep guard). There is no similar guard for `challengePeriod`, so any legitimate reduction of it — which governance is fully expected to perform over the life of the protocol — opens this window for every already-stored, not-yet-finalized height. The exploitation step itself only requires calling the public `HandlerV2` entrypoints, which any relayer can already do.

### Recommendation
Snapshot the challenge period that applies to a state commitment at the time it is stored (e.g., store it alongside `_stateCommitmentsUpdateTime`) and use that snapshotted value in `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts`, and the Rust `verify_delay_passed`, instead of re-reading the live/global `challengePeriod`. Alternatively, enforce that a `challengePeriod` decrease only applies to commitments stored *after* the update, and/or require a timelock/buffer before a shortened challenge period can be used against already-pending commitments.

### Proof of Concept
1. A relayer stores a (possibly incorrect or fraud-provable) state commitment at height `H` via `storeStateMachineCommitment`, with `challengePeriod = 7 days` in effect.
2. Governance dispatches `updateHostParams` reducing `challengePeriod` to `0` or a small value (a normal, non-malicious tuning action) via `HostManager.onAccept` → `IHostManager.updateHostParams`. [7](#0-6) 
3. Immediately after, any relayer calls `HandlerV2.handlePostRequests` (or `handleGetResponses`/timeout variants) referencing proof height `H`. The check `challengePeriod > delay` now passes trivially because `challengePeriod` was just lowered, even though `delay` (time since `H` was stored) is far shorter than the originally-promised 7-day fraud-proof window. [1](#0-0) 
4. Requests/responses/timeouts derived from the still-disputable commitment at `H` are dispatched to destination modules before the community had the chance to submit a fraud proof under the original timing guarantee — an instance of false state acceptance with no attacker privilege beyond calling a public handler function.

### Citations

**File:** evm/src/core/HandlerV2.sol (L182-186)
```text
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

**File:** evm/src/core/EvmHost.sol (L573-636)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }

    /**
     * @dev Updates the HostParams. Will reset all fishermen accounts and initialize any new state machines.
     * @param params, the new host params.
     */
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
