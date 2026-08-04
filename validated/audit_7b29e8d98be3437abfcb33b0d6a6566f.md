## Title
`updateHostParamsInternal` enforces a minimum threshold for `unStakingPeriod` but not for `challengePeriod`, allowing the fisherman fraud-proof window to be silently disabled - (`evm/src/core/EvmHost.sol`)

### Summary
The external report's core defect is a setter (`setMaxReferralEarningTime`) that writes an unchecked value into a security-critical time comparison, letting the intended waiting-period requirement be bypassed. Hyperbridge's EVM host has the same class of defect: `updateHostParamsInternal` validates several `HostParams` fields (addresses, `stateMachines.length`, and explicitly `unStakingPeriod >= 1 days`) but performs **no bound check whatsoever on `challengePeriod`**, even though `challengePeriod` gates every proof-acceptance path in `HandlerV2.sol` via an explicit "0 means skip the wait" special case.

### Finding Description
`HostParams.challengePeriod` is described in code as the "Minimum challenge period for state commitments in seconds" [1](#0-0) . It is updated exclusively through `updateHostParams`/`updateHostParamsInternal`, which is reachable only via the configured `hostManager` [2](#0-1) .

`updateHostParamsInternal` validates `hostManager`, `handler`, `consensusClient`, `hyperbridge`, `stateMachines.length`, and enforces a hard floor on `unStakingPeriod` (`if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();`), but it copies `params.challengePeriod` straight into storage with **no minimum-value check at all**: [3](#0-2) 

Meanwhile, every message-processing entry point in `HandlerV2.sol` treats `challengePeriod == 0` as "the wait requirement is entirely disabled": [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

The same "0 disables the check" pattern also exists on the Substrate side in `verify_delay_passed`: `delay_period.as_secs() == 0 || ...` [8](#0-7) , and `store_challenge_period` accepts any `u64` including `0` unconditionally [9](#0-8) .

This is structurally identical to the reported bug: a setter that writes a value used later as `X >= threshold` gate, with no floor defined on the setter itself, and the underlying value's magic zero value has special bypass semantics baked into the consumer. Unlike `unStakingPeriod`, which the developers clearly recognized needs a floor and hard-coded `1 days`, `challengePeriod` has no analogous protection despite being the parameter that gives fishermen time to submit fraud proofs and veto malicious `StateCommitment`s before they are trusted by `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts`.

### Impact Explanation
`challengePeriod` is the sole mechanism that lets fishermen detect and veto fraudulent `StateCommitment`s before they are relied upon to dispatch incoming requests/responses or process timeouts [10](#0-9) . If it is ever configured at or near zero (accidentally, through a malformed governance payload, encoding mistake, or a bug in the parameter-update tooling), the destination chain will accept any state commitment as canonical the instant it's stored, with no fraud-proof window — directly enabling false state acceptance and unauthorized dispatch of requests/responses/timeouts before any fisherman challenge can occur. Given the intentional `!= 0` bypass semantics in the consumer code, this is a hair-trigger rather than a defense-in-depth gap.

### Likelihood Explanation
Medium: exploitation requires the value to actually reach zero (or an unreasonably small value) through the `hostManager`/governance update path, since there is no other way to set `challengePeriod`. There is no independent on-chain guard preventing this, unlike `unStakingPeriod`, which the code explicitly protects. The absence of any threshold check is a genuine, provable gap in `updateHostParamsInternal`.

### Recommendation
Add an explicit minimum-bound check on `params.challengePeriod` in `updateHostParamsInternal` (mirroring the `unStakingPeriod` pattern), and apply the analogous fix to `store_challenge_period` on the Substrate side (or to `update_consensus_state`'s dispatch call) so a zero/near-zero challenge period can never be persisted. Consider also removing the "`challengePeriod == 0` disables the check" special case in `HandlerV2.sol` and `verify_delay_passed`, since a magic value with security-disabling semantics is inherently risky.

### Proof of Concept
1. Cross-chain governance (via `HostManager.onAccept`) sends a `SetHostParam` action with `HostParams.challengePeriod = 0`.
2. `updateHostParams` → `updateHostParamsInternal` accepts this value unconditionally — [11](#0-10)  — because there is no floor check on `challengePeriod` (contrast with the `unStakingPeriod` check on the line immediately above it).
3. A relayer submits a `PostRequestMessage` immediately after a new `StateCommitment` is stored (zero elapsed delay).
4. In `handlePostRequests`, `challengePeriod = host.challengePeriod()` returns `0`, so `if (challengePeriod != 0 && challengePeriod > delay)` is `false` and `ChallengePeriodNotElapsed` never reverts — [12](#0-11)  — allowing the request to be dispatched to the destination module before any fisherman had a chance to veto the commitment.

### Citations

**File:** evm/src/core/EvmHost.sol (L56-60)
```text
    // The unstaking period of Polkadot's validators. In order to prevent long-range attacks
    uint256 unStakingPeriod;
    // Minimum challenge period for state commitments in seconds;
    uint256 challengePeriod;
    // The consensus client contract which handles consensus proof verification
```

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

**File:** evm/src/core/EvmHost.sol (L581-636)
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
```

**File:** evm/src/core/HandlerV2.sol (L181-186)
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

**File:** modules/pallets/ismp/src/host.rs (L293-300)
```rust
	fn store_challenge_period(
		&self,
		state_machine: StateMachineId,
		period: u64,
	) -> Result<(), Error> {
		ChallengePeriod::<T>::insert(state_machine, period);
		Ok(())
	}
```

**File:** docs/content/protocol/ismp/consensus.mdx (L203-229)
```text
### `StateMachineUpdated`

```rust showLineNumbers
/// Emitted when a state machine is successfully updated to a new height
struct StateMachineUpdated {
    /// State machine height
    state_machine_id: StateMachineId,
    /// State machine latest height
    latest_height: u64,
}
```

A `StateMachineUpdated` event is emitted to notify network participants (both relayers and fishermen) of some newly available `StateCommitment`s for a given state machine. Relayers will wait for the configured `challenge_period` before attempting to transmit new requests & responses. While fishermen will check if these pending `StateCommitment`s describe valid states on the counterparty network. If the `challenge_period` elapses without any fraud proofs being presented, we can safely conclude that the provided `StateCommitment`s are indeed canonical.

### `StateCommitmentVetoed`

```rust showLineNumbers
/// Emitted when a `StateCommitment` has been successfully vetoed by a fisherman
pub struct StateCommitmentVetoed {
    /// The state commitment identifier
    pub height: StateMachineHeight,
    /// The account responsible
    pub fisherman: Vec<u8>,
}
```

A `StateCommitmentVetoed` event is emitted after a fisherman successfully vetoes a `StateCommitment` that is still within its challenge period. This instructs relayers to discard any pending requests/responses whose proofs rely on the vetoed commitment.
```
