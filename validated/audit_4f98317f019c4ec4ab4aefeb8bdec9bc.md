## Analysis

**Core broken invariant (from the seed report):** `BondBaseSDA.setDefaults` was an authorized setter that wrote multiple related config fields without validating their required relationships, silently breaking a downstream safety invariant (markets became uncreatable / decay logic broke).

**Local analog:** `EvmHost.updateHostParamsInternal` in `evm/src/core/EvmHost.sol` is the equivalent authorized config setter for Hyperbridge's `HostParams`. It validates several fields (host manager, handler, consensus client addresses/interfaces, hyperbridge id, state machine list, and `unStakingPeriod >= 1 day`), but it never validates `challengePeriod`. [1](#0-0) 

`challengePeriod` is not a cosmetic value — it is the core fraud-proof/veto window that every proof-consuming path in `HandlerV2.sol` relies on to decide whether a state commitment can be trusted yet: [2](#0-1) [3](#0-2) 

Each of these checks uses the pattern `if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();`. Note the `challengePeriod != 0` short-circuit — if `challengePeriod` is `0`, the delay check is skipped entirely for post requests, get responses, and both timeout paths, meaning a state commitment becomes immediately actionable with zero challenge/veto window. The same `0`-disables-the-check pattern is also codified in the SDK's `waitForChallengePeriod` helper and the Substrate equivalent `verify_delay_passed`, confirming `challengePeriod == 0` is a recognized "disable the safety window" sentinel throughout the codebase rather than an edge case that was overlooked in one place: [4](#0-3) [5](#0-4) 

Unlike `unStakingPeriod`, which is explicitly floored at 1 day (`if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();`), `challengePeriod` has no floor, no relationship check against `unStakingPeriod`, and can be set to `0` (or any arbitrarily small value) via `updateHostParamsInternal` with no revert. This mirrors the Bond report exactly: an authorized setter mutates multiple interdependent security parameters, validates some of them, but omits validation of the one field whose value directly disables an invariant relied upon elsewhere in the system.

### Title
`EvmHost.updateHostParamsInternal` does not validate `challengePeriod`, allowing the fraud-proof/veto window to be silently disabled - (File: evm/src/core/EvmHost.sol)

### Summary
`updateHostParamsInternal` validates `hostManager`, `handler`, `consensusClient`, `hyperbridge`, `stateMachines`, and `unStakingPeriod`, but performs no validation on `challengePeriod`. Every downstream proof-consuming handler in `HandlerV2.sol` treats `challengePeriod == 0` as "skip the delay check," so an update that sets `challengePeriod` to `0` (or leaves it unset/zero by omission in a params struct) removes the fisherman-veto safety window for accepting state commitments and processing requests, responses, and timeouts.

### Finding Description
`updateHostParamsInternal` is the sole gate for mutating `HostParams`, reachable via `updateHostParams` (restricted to `_hostParams.hostManager`, i.e. cross-chain governance requests routed through `HostManager.onAccept`) and, on `TestnetHost`, additionally by the admin. [6](#0-5) [7](#0-6) 

Within `updateHostParamsInternal`, address/interface checks and the `unStakingPeriod` floor are enforced, but `params.challengePeriod` is copied directly into storage with no bound: [8](#0-7) 

Once `challengePeriod` is `0`, `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts` all bypass the elapsed-time gate because of the `challengePeriod != 0 &&` short-circuit: [9](#0-8) [10](#0-9) 

The rest of the protocol — off-chain relayers/tesseract, and the SDK — assume the challenge period is a meaningful non-trivial delay that gives fishermen time to veto a bad state commitment before it is used to justify request/response/timeout dispatch: [11](#0-10) 

There is no cross-field validation comparable to Bond Protocol's fix (which required relationships like `defaultTuneInterval >= minDepositInterval`) tying `challengePeriod` to `unStakingPeriod`, the consensus finality window, or any non-zero floor.

### Impact Explanation
A malformed/incorrect `challengePeriod` update (whether from a misconfigured `HostParams` payload, an incorrectly abi-decoded value, or an update that omits/zeroes the field) causes every proof-handling function in `HandlerV2` to treat state commitments as immediately final, with no veto window. This directly enables false-state-derived request/response/timeout processing before the state commitment has actually had a chance to be challenged — the exact "false proof/state acceptance" impact class called out by the bounty scope, since the challenge period is Hyperbridge's core defense binding state commitments to a safety delay.

### Likelihood Explanation
The path is reached through the standard, intended `updateHostParams` flow that is expected to fire during normal parameter maintenance (fee token changes, address rotations, etc. bundled in the same struct). Because `updateHostParamsInternal` validates several unrelated fields and gives the impression of being a fully-guarded setter, an operator/governance update that legitimately changes other fields (e.g., rotating `handler` or `consensusClient`) while leaving/passing `challengePeriod` as `0` will pass all existing checks silently — there is no revert to signal the omission, unlike the explicit `InvalidUnstakingPeriod` guard for the sibling field.

### Recommendation
Add a floor/consistency check for `challengePeriod` in `updateHostParamsInternal`, analogous to the existing `unStakingPeriod` guard, e.g. require `challengePeriod` to be non-zero (or above a defined minimum) and sane relative to `unStakingPeriod`, reverting with a new `InvalidChallengePeriod` error if violated.

### Proof of Concept
1. Cross-chain governance (or, on `TestnetHost`, the admin) calls `updateHostParams`/`onAccept(SetHostParam)` with a `HostParams` struct identical to the current one except `challengePeriod = 0`. [12](#0-11) 
2. `updateHostParamsInternal` passes all existing checks (`hostManager`, `handler`, `consensusClient`, `hyperbridge`, `stateMachines`, `unStakingPeriod`) since none of them constrain `challengePeriod`, and stores `_hostParams.challengePeriod = 0`. [13](#0-12) 
3. A relayer immediately calls `handlePostRequests` (or any of the other three handlers) for a state commitment that was just stored this same block. `delay = 0`, but since `challengePeriod == 0`, the guard `challengePeriod != 0 && challengePeriod > delay` evaluates to `false`, so `ChallengePeriodNotElapsed` is never raised and the request is dispatched without any veto window having existed. [14](#0-13)

### Citations

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

**File:** evm/src/core/EvmHost.sol (L581-633)
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

**File:** sdk/packages/sdk/src/utils.ts (L68-73)
```typescript
export async function waitForChallengePeriod(chain: IChain, stateMachineHeight: StateMachineHeight): Promise<void> {
	// Get the challenge period for this state machine
	const challengePeriod = await chain.challengePeriod(stateMachineHeight.id)

	if (challengePeriod === BigInt(0)) return

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

**File:** tesseract/messaging/primitives/src/lib.rs (L731-746)
```rust
pub async fn wait_for_challenge_period(
	client: Arc<dyn IsmpProvider>,
	last_consensus_update: Duration,
	counterparty_state_id: StateMachineId,
) -> anyhow::Result<()> {
	let challenge_period = client.query_challenge_period(counterparty_state_id).await?;
	if challenge_period != Duration::ZERO {
		log::info!(
			target: LOG_TARGET, "Waiting for challenge period {challenge_period:?} for {} on {}",
			counterparty_state_id.state_id,
			client.name()
		);
	}

	tokio::time::sleep(challenge_period).await;
	let current_timestamp = client.query_timestamp().await?;
```
