### Title
`challengePeriod == 0` is treated as "disable the fraud-proof delay" and is never lower-bounded, letting relayers finalize state and drain requests/responses/timeouts before fishermen can veto - ([File: evm/src/core/HandlerV2.sol])

### Summary
This mirrors the ForkDAODeployer bug-class exactly: a governance-configurable numeric parameter that gates a critical safety window has no minimum-bound validation at the point it is set, and the value `0` has an implicit "disable the guard" meaning baked into every consumer of that parameter. In `ForkDAODeployer`, `delayedGovernanceMaxDuration = 0` silently bypasses `checkGovernanceActive()`. In Hyperbridge, `HostParams.challengePeriod = 0` silently bypasses the fraud-proof/veto window in every `HandlerV2` entrypoint, via the explicit special-case `challengePeriod != 0 && challengePeriod > delay`.

### Finding Description
`EvmHost.updateHostParamsInternal` validates several `HostParams` fields (`hostManager`, `handler`, `consensusClient`, `hyperbridge`, `stateMachines.length`, and `unStakingPeriod >= 1 days`) but performs **no validation whatsoever on `challengePeriod`**: [1](#0-0) 

Every permissionless handler entrypoint that consumes a state commitment treats `challengePeriod == 0` as "skip the delay check entirely" rather than "delay of zero seconds is still checked normally against `delay`": [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The challenge period is Hyperbridge's fisherman-veto window: after a state machine commitment is stored via `storeStateMachineCommitment` (called from `handleConsensus`), fishermen have `challengePeriod` seconds to call `deleteStateMachineCommitmentInternal` and veto a fraudulent/byzantine commitment before it can be relied upon by `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, or `handleGetRequestTimeouts`: [6](#0-5) 

Once `challengePeriod` is set to `0` (accidental misconfiguration by governance/hostManager — same "operational mistake" scenario acknowledged in the ForkDAODeployer report, not a malicious actor), the guard in every handler function evaluates `challengePeriod != 0` as `false` and unconditionally skips `revert ChallengePeriodNotElapsed()`. This means the moment a state commitment is recorded (even in the same block as `handleConsensus`), any relayer can immediately submit `handlePostRequests`/`handleGetResponses`/timeout messages referencing that height, with zero opportunity for a fisherman to veto it first. The Rust ISMP core has an analogous, arguably intentional pattern (`delay_period.as_secs() == 0 || ...` in `verify_delay_passed`), reinforcing that `0` is a recognized "disable" sentinel across the codebase rather than a validated bound: [7](#0-6) 

### Impact Explanation
This is a false-state-acceptance / unauthorized-execution primitive matching the bounty's core invariant: "Consensus proofs, state proofs, challenge periods... must never let false remote state become trusted." With `challengePeriod = 0`, the entire fraud-proof window collapses to nothing, so a state commitment derived from a technically-valid-but-byzantine consensus update (or one that fishermen would otherwise veto) becomes immediately actionable. An attacker (any permissionless relayer) can race `handlePostRequests`/`handleGetResponses` calls against the fisherman veto, dispatching malicious cross-chain requests/responses that get executed by destination modules before the state commitment can be deleted — a direct false-state-acceptance and unauthorized-execution path with fund-loss potential in downstream `IApp`s (e.g., `IntentGatewayV2`, `HostManager.withdraw`).

### Likelihood Explanation
Medium: like the original report, this requires the parameter to be set to `0` at some point (via cross-chain governance `updateHostParams`/`HostManager.onAccept`, or `TestnetHost` admin path). This is not a "malicious governance actor" scenario per se — it is an unguarded, easy-to-miss operational parameter (a `uint256` with no minimum) buried in a large `HostParams` struct alongside many other fields that *are* validated (`unStakingPeriod` has an explicit 1-day floor, but `challengePeriod` does not), making an accidental zero-value misconfiguration plausible and, once it happens, trivially and permissionlessly exploitable by any relayer with no other capability required.

### Recommendation
Add an explicit lower bound check for `challengePeriod` in `updateHostParamsInternal` (mirroring the existing `InvalidUnstakingPeriod` pattern for `unStakingPeriod`), e.g. revert if `challengePeriod == 0` (or below a hard-coded minimum such as 30 minutes). Additionally, remove the `challengePeriod != 0` bypass branch in `HandlerV2.sol` so that even if a zero value slips through, the delay comparison (`challengePeriod > delay`) is evaluated normally rather than being treated as "disabled."

### Proof of Concept
1. Cross-chain governance (via `HostManager.onAccept` → `IHostManager.updateHostParams`) submits a `HostParams` update with `challengePeriod = 0`; `updateHostParamsInternal` accepts it since no check exists for this field: [8](#0-7) 
2. A relayer submits a consensus proof via `handleConsensus`, which calls `host.storeStateMachineCommitment` for a new height, setting `_stateCommitmentsUpdateTime` to `block.timestamp`: [9](#0-8) [10](#0-9) 
3. In the same block/transaction batch (via `batchCall`), the relayer calls `handlePostRequests` with that same `proof.height`. `delay = 0`, `challengePeriod = 0`, so `challengePeriod != 0 && challengePeriod > delay` is `false` — the `ChallengePeriodNotElapsed` check is bypassed entirely: [11](#0-10) [12](#0-11) 
4. Requests are dispatched to destination `IApp` modules with no fisherman having had any window to veto the underlying state commitment, before `deleteStateMachineCommitmentInternal` could ever be called.

### Citations

**File:** evm/src/core/EvmHost.sol (L581-621)
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
```

**File:** evm/src/core/EvmHost.sol (L683-732)
```text
    /**
     * @dev Store the state commitment at given state height alongside relevant metadata.
     * Assumes the state commitment is of the latest height.
     */
    function storeStateMachineCommitment(StateMachineHeight memory height, StateCommitment memory commitment)
        external
        restrict(_hostParams.handler)
    {
        _stateCommitments[height.stateMachineId][height.height] = commitment;
        _stateCommitmentsUpdateTime[height.stateMachineId][height.height] = block.timestamp;
        _latestStateMachineHeight[height.stateMachineId] = height.height;

        emit StateMachineUpdated({
            stateMachineId: this.stateMachineId(_hostParams.hyperbridge, height.stateMachineId), 
            height: height.height
        });
    }

    /**
     * @dev Delete the state commitment at given state height.
     */
    function deleteStateMachineCommitment(StateMachineHeight memory height, address fisherman)
        external
        restrict(_hostParams.handler)
    {
        deleteStateMachineCommitmentInternal(height, fisherman);
    }

    /**
     * @dev Delete the state commitment at given state height.
     */
    function deleteStateMachineCommitmentInternal(StateMachineHeight memory height, address fisherman) internal {
        StateCommitment memory stateCommitment = _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitmentsUpdateTime[height.stateMachineId][height.height];
        // technically any state commitment can be vetoed, safety check that it's the latest before resetting it.
        if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
            _latestStateMachineHeight[height.stateMachineId] = 1;
        }

        // track the fisherman responsible for rewards on hyperbridge through state proofs
        _vetoes[height.stateMachineId][height.height] = fisherman;

        emit StateCommitmentVetoed({
            stateMachineId: this.stateMachineId(_hostParams.hyperbridge, height.stateMachineId),
            stateCommitment: stateCommitment,
            height: height.height,
            fisherman: fisherman
        });
    }
```

**File:** evm/src/core/HandlerV2.sol (L129-135)
```text
    function batchCall(bytes[] memory calls) external {
        uint256 len = calls.length;
        for (uint256 i = 0; i < len; ++i) {
            (bool success, bytes memory returnData) = address(this).delegatecall(calls[i]);
            if (!success) revert BatchCallFailed(i, returnData);
        }
    }
```

**File:** evm/src/core/HandlerV2.sol (L144-164)
```text
    function handleConsensus(IHost host, bytes calldata proof) external notFrozen(host) {
        uint256 delay = block.timestamp - host.consensusUpdateTime();
        if (delay >= host.unStakingPeriod()) revert ConsensusClientExpired();

        bytes memory previousState = host.consensusState();
        (bytes memory verifiedState, IntermediateState[] memory intermediates, uint256 nextAuthoritySetId) =
            IConsensusV2(host.consensusClient()).verify(previousState, proof);

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

**File:** evm/src/core/HandlerV2.sol (L181-210)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        uint256 requestsLen = request.requests.length;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](requestsLen);

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // check destination
            if (!leaf.request.dest.equals(host.host())) revert InvalidMessageDestination();
            // check time-out
            if (timestamp >= leaf.request.timeout()) revert MessageTimedOut();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.request.hash());
        }

        bytes32 root = host.stateMachineCommitment(request.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, request.proof.multiproof, leaves, request.proof.leafCount);
        if (!valid) revert InvalidProof();

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // duplicate request?
            if (host.requestReceipts(leaf.request.hash()) != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.request, _msgSender());
        }
    }
```

**File:** evm/src/core/HandlerV2.sol (L217-247)
```text
    function handleGetResponses(IHost host, GetResponseMessage calldata message) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(message.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        uint256 responsesLength = message.responses.length;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](responsesLength);

        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // don't check for timeouts because it's checked on Hyperbridge

            // known request? also serves as source check
            FeeMetadata memory meta = host.requestCommitments(leaf.response.request.hash());
            if (meta.sender == address(0)) revert UnknownMessage();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.response.hash());
        }

        bytes32 root = host.stateMachineCommitment(message.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, message.proof.multiproof, leaves, message.proof.leafCount);
        if (!valid) revert InvalidProof();

        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // duplicate response?
            if (host.responseReceipts(leaf.response.request.hash()).relayer != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.response, _msgSender());
        }
    }
```

**File:** evm/src/core/HandlerV2.sol (L254-286)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            PostRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            // known request? also serves as source check
            bytes32 requestCommitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(requestCommitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(REQUEST_RECEIPTS_STORAGE_PREFIX, requestCommitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(PostRequestTimeout(request, _msgSender()), meta, requestCommitment);
        }
    }
```

**File:** evm/src/core/HandlerV2.sol (L293-321)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            GetRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            bytes32 commitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(commitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(RESPONSE_RECEIPTS_STORAGE_PREFIX, commitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(GetRequestTimeout(request, _msgSender()), meta, commitment);
        }
    }
```

**File:** modules/ismp/core/src/handlers.rs (L103-114)
```rust
/// for the state machine has elasped.
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
