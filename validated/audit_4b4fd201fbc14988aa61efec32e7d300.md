### Title
Single global `challengePeriod` applied to all counterparty state machines on `EvmHost` allows premature message processing - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost` stores exactly one `challengePeriod` value in `HostParams` [1](#0-0)  and `HandlerV2` uses this single scalar, via `host.challengePeriod()`, to gate the processing of requests, responses, and timeouts from *every* counterparty state machine registered on the host [2](#0-1) [3](#0-2) . This is the same bug class as the reported Chainlink issue: one time-interval reused for multiple sources with materially different required delay/finality characteristics, instead of a per-source value.

By contrast, the ISMP core/pallet side of the protocol correctly keys the challenge period by `StateMachineId` (state id + consensus state id), both in the trait definition and pallet storage [4](#0-3) [5](#0-4) , and `CreateConsensusState`/`UpdateConsensusState` messages explicitly carry a `BTreeMap<StateMachine, u64>` of per-chain challenge periods [6](#0-5) [7](#0-6) . The EVM host implementation collapses this per-state-machine design into a single global field, defeating the intended granularity.

### Finding Description
`HostParams.challengePeriod` is a single `uint256` for the entire `EvmHost` deployment [8](#0-7) , even though the same host accepts state commitments from multiple distinct `stateMachines` [9](#0-8) , each of which may be secured by fundamentally different consensus/finality mechanisms (e.g. GRANDPA finality relayed via BEEFY vs. an Optimism fault-proof game state machine) with very different fraud/veto windows — as seen in the consensus-host configs that each set their own `challenge_periods` map when bootstrapping a client [10](#0-9) [11](#0-10) .

In `HandlerV2.sol`, every message-processing entrypoint (`handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts`) computes `delay = block.timestamp - host.stateMachineCommitmentUpdateTime(proof.height)` and compares it against the single `host.challengePeriod()` value, regardless of which `stateMachineId` the proof height belongs to [2](#0-1) [3](#0-2) . There is no lookup keyed by the source state machine's identity, unlike `verify_delay_passed` on the Substrate/ISMP-core side, which explicitly fetches `host.challenge_period(proof_height.id)` per `StateMachineId` before allowing further processing [12](#0-11) .

Because the EVM host's operator must pick one `challengePeriod` for all counterparty state machines, whichever value is configured is necessarily too short for at least one class of counterparty chain if the deployment supports state machines with heterogeneous finality/dispute windows (e.g., a chain with a short veto/fraud-proof window and a chain requiring a much longer window before its state commitment can be trusted). Requests, responses, and timeouts referencing a `StateMachineHeight` from the chain that actually needs a longer delay can be processed by `HandlerV2` as soon as the shared (shorter) `challengePeriod` elapses, even though that specific state machine's commitment has not yet cleared its real dispute/finality window.

### Impact Explanation
If the configured global challenge period is shorter than what a specific counterparty state machine actually requires for its state commitment to be considered final/undisputed, messages (post requests, get responses, timeouts) can be dispatched into destination modules based on a state commitment that is still within its fraud/veto window on that chain. This is a false-state-acceptance primitive: `dispatchIncoming`/`dispatchTimeOut` on `EvmHost` will act on state that could still be reverted or vetoed, which can lead to unauthorized execution, incorrect fund releases, or acceptance of a state root that is later proven invalid — directly matching the bounty's "false proof/state acceptance" and "unauthorized execution" categories.

### Likelihood Explanation
This is not a peer/relayer-misbehavior issue — it is a structural consequence of `HostParams` design and requires no malicious actor, only a normal deployment/configuration event: a host operator (or governance via `updateHostParams`) adding a new counterparty `stateMachine` with different finality characteristics without being able to express a distinct challenge period for it, because the ABI/storage layout only supports one scalar. Any unprivileged relayer can then submit `handlePostRequests`/`handleGetResponses`/timeout messages the moment the (too-short, shared) `challengePeriod` elapses, exploiting the gap for the specific state machine that needed more time.

### Recommendation
Change `challengePeriod` from a single `uint256` in `HostParams` to a mapping keyed by state machine identifier (mirroring the Substrate/pallet `ChallengePeriod: StorageMap<_, _, StateMachineId, u64>` design [4](#0-3) ), and update `HandlerV2.sol`'s delay checks in `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts` to look up the challenge period for the specific `stateMachineId` referenced in `proof.height`/`message.height`, instead of calling the parameterless `host.challengePeriod()`.

### Proof of Concept
1. Operator deploys `EvmHost` with `stateMachines = [ChainA, ChainB]` and a single `challengePeriod = 5 minutes` (matching ChainA's fast/short dispute window, e.g. the 5*60 default seen in `op-host`/`pharos` bootstrap configs [13](#0-12) ).
2. ChainB actually requires a longer window (e.g. it uses a fault-proof/veto mechanism with a longer contestation period) before its `StateMachineCommitment` should be trusted.
3. A relayer submits a `ConsensusMessage`, `storeStateMachineCommitment` is recorded for ChainB with `_stateCommitmentsUpdateTime` = `block.timestamp` [14](#0-13) .
4. After only 5 minutes (the shared `challengePeriod`), any relayer calls `handlePostRequests` with a proof referencing ChainB's commitment height; `delay >= challengePeriod` passes the check in `HandlerV2.sol` line 184-185 even though ChainB's real dispute window has not elapsed [15](#0-14) .
5. The request is dispatched to the destination module based on a still-contestable state commitment, before the equivalent Substrate-side logic (which would correctly key by `StateMachineId`) would have permitted it.

### Citations

**File:** evm/src/core/EvmHost.sol (L41-66)
```text
struct HostParams {
    // The fee token contract address. This will typically be DAI.
    // but we allow it to be configurable to prevent future regrets.
    address feeToken;
    // The admin account, this only has the rights to freeze, or unfreeze the bridge
    address admin;
    // Ismp message handler contract. This performs all verification logic
    // needed to validate cross-chain messages before they are dispatched to local modules
    address handler;
    // The authorized host manager contract, is itself an `IApp`
    // which receives governance requests from the Hyperbridge chain to either
    // withdraw revenue from the host or update its protocol parameters
    address hostManager;
    // The local UniswapV2Router02 contract, used for swapping the native token to the feeToken.
    address uniswapV2;
    // The unstaking period of Polkadot's validators. In order to prevent long-range attacks
    uint256 unStakingPeriod;
    // Minimum challenge period for state commitments in seconds;
    uint256 challengePeriod;
    // The consensus client contract which handles consensus proof verification
    address consensusClient;
    // State machines whose state commitments are accepted
    uint256[] stateMachines;
    // The state machine identifier for hyperbridge
    bytes hyperbridge;
}
```

**File:** evm/src/core/EvmHost.sol (L687-699)
```text
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
```

**File:** evm/src/core/HandlerV2.sol (L181-221)
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

    /**
     * @dev check response proofs, message delay and timeouts, then dispatch get responses to modules
     * @param host - Ismp host
     * @param message - batch get responses
     */
    function handleGetResponses(IHost host, GetResponseMessage calldata message) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(message.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** evm/src/core/HandlerV2.sol (L254-296)
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

    /**
     * @dev Check the provided Get request timeouts, then dispatch to modules
     * @param host - Ismp host
     * @param message - batch get request timeouts
     */
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** modules/pallets/ismp/src/lib.rs (L204-208)
```rust
	/// A mapping of state machine Ids to their challenge periods
	#[pallet::storage]
	#[pallet::getter(fn challenge_period)]
	pub type ChallengePeriod<T: Config> =
		StorageMap<_, Blake2_128Concat, StateMachineId, u64, OptionQuery>;
```

**File:** modules/ismp/core/src/handlers.rs (L102-114)
```rust
/// This function checks to see that the delay period configured on the host chain
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

**File:** modules/ismp/core/src/messaging.rs (L98-114)
```rust
pub struct CreateConsensusState {
	/// Scale encoded consensus state
	#[serde(with = "serde_hex_utils::as_hex")]
	pub consensus_state: Vec<u8>,
	/// Consensus client id
	#[serde(with = "serde_hex_utils::as_utf8_string")]
	pub consensus_client_id: ConsensusClientId,
	/// The consensus state Id
	#[serde(with = "serde_hex_utils::as_utf8_string")]
	pub consensus_state_id: ConsensusStateId,
	/// Unbonding period for this consensus state.
	pub unbonding_period: u64,
	/// Challenge period for the supported state machines
	pub challenge_periods: BTreeMap<StateMachine, u64>,
	/// State machine commitments
	pub state_machine_commitments: Vec<(StateMachineId, StateCommitmentHeight)>,
}
```

**File:** modules/pallets/ismp/src/utils.rs (L33-44)
```rust
/// Params to update the unbonding period for a consensus state
#[derive(
	Debug, Clone, Encode, Decode, DecodeWithMemTracking, scale_info::TypeInfo, PartialEq, Eq,
)]
pub struct UpdateConsensusState {
	/// Consensus state identifier
	pub consensus_state_id: ConsensusStateId,
	/// Unbonding duration
	pub unbonding_period: Option<u64>,
	/// Challenge period duration for different state machines
	pub challenge_periods: BTreeMap<StateMachine, u64>,
}
```

**File:** tesseract/consensus/beefy/src/host.rs (L370-380)
```rust
		Ok(Some(CreateConsensusState {
			consensus_state: consensus_state.abi_encode(),
			consensus_client_id: *b"BEEF",
			consensus_state_id: self.config.consensus_state_id,
			unbonding_period: 60 * 60 * 60 * 27,
			challenge_periods: vec![(self.client.state_machine_id().state_id, 5 * 60)]
				.into_iter()
				.collect(),
			state_machine_commitments: vec![],
		}))
	}
```

**File:** tesseract/consensus/op-host/src/host.rs (L207-217)
```rust
		Ok(Some(CreateConsensusState {
			consensus_state: initial_consensus_state.encode(),
			consensus_client_id: OPTIMISM_CONSENSUS_CLIENT_ID,
			consensus_state_id: self.consensus_state_id,
			unbonding_period: u64::MAX,
			challenge_periods: state_machine_commitments
				.iter()
				.map(|(state_machine, ..)| (state_machine.state_id, 5 * 60))
				.collect(),
			state_machine_commitments,
		}))
```
