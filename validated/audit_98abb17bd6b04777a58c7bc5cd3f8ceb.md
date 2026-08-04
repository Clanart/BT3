### Title
Retroactive Global Challenge-Period Reduction Bypasses Fraud-Proof Window for Already-Stored State Commitments - ([File: evm/src/core/HandlerV2.sol])

### Summary
`EvmHost` stores a single global `challengePeriod` value in `HostParams`, and this value can be changed at any time by the `hostManager` via cross-chain governance [1](#0-0) . The problem is that `HandlerV2.sol`'s message-processing functions compute the elapsed delay against the *current* `challengePeriod()` value at call time, not the value that was in force when the specific `StateMachineHeight` commitment was stored [2](#0-1) . This is the same broken invariant as the RocketDAOProposals report: a timing/delay parameter that governs when an action becomes valid can be shortened and applied retroactively to state that already exists, letting privileged actors collapse the safety window for messages that are already in flight.

### Finding Description
Every `StateMachineHeight` commitment stored via `storeStateMachineCommitment` is meant to be trusted only after `challengePeriod` has elapsed since its `stateMachineCommitmentUpdateTime`, giving fishermen/watchers time to veto fraudulent state [3](#0-2) . However, the check in `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts` all read `host.challengePeriod()` live at call time:

```solidity
uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
uint256 challengePeriod = host.challengePeriod();
if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
``` [2](#0-1) 

The same pattern repeats for responses [4](#0-3) , post-request timeouts [5](#0-4) , and get-request timeouts [6](#0-5) .

`challengePeriod` is not pinned per commitment — it is a single mutable field in `HostParams` that `updateHostParams` overwrites wholesale via `updateHostParamsInternal`, with no validation on the new value (it can be set to `0` or any arbitrarily small number) and no check against commitments already stored for older heights:

```solidity
_hostParams.challengePeriod = params.challengePeriod;
``` [7](#0-6) 

`updateHostParamsInternal` validates `hostManager`, `handler`, `consensusClient`, `hyperbridge` id, `stateMachines` length, and `unStakingPeriod` (must be ≥ 1 day), but performs **no minimum-bound check on `challengePeriod`** [8](#0-7) . This call is only reachable through `HostManager.onAccept`, which is itself gated only by `request.source.equals(hyperbridge())` — i.e., any cross-chain governance PostRequest whose `source` state machine matches the configured Hyperbridge coprocessor id is accepted and forwarded unchanged into `updateHostParams` [9](#0-8) .

The Rust equivalent mirrors this design: `EvmHostParam::update` unconditionally overwrites `challenge_period` field-by-field with whatever the incoming governance message specifies, with no floor [10](#0-9) , and the accompanying test confirms an arbitrary new `challengePeriod` value takes effect immediately and globally the moment the governance request is processed [11](#0-10) .

By contrast, the off-chain relayer code (`tesseract`) treats `challengePeriod` as if it were fixed at the time the state machine height was observed — it queries the challenge period once and sleeps for that duration [12](#0-11) , and fishermen rely on this same assumption to have time to detect and veto a bad state commitment before it becomes actionable. If `challengePeriod` is reduced (or zeroed) after a state commitment has already been stored but before the *old* challenge period has elapsed, `handlePostRequests`/`handleGetResponses`/timeout handlers will immediately accept messages proven against that commitment, because the on-chain check only cares about the delay versus the *current* parameter — not the parameter that was active when the commitment was recorded.

### Impact Explanation
This breaks the "false proof/state acceptance" invariant explicitly called out in the bounty scope: a state commitment stored under a long challenge period (e.g. a governance-committed 7-day fraud window) can be collapsed to near-zero at any moment, allowing requests/responses/timeouts proven against a commitment that had not yet passed its intended vetting window to be dispatched to destination modules. Since `dispatchIncoming` and `dispatchTimeOut` ultimately move funds/execute cross-chain effects (e.g., intent settlement, paymaster refunds, asset transfers) based on the accepted commitment, this can result in acceptance of state that a fisherman had not yet had the originally-promised time to veto, defeating the entire purpose of the challenge period and enabling unauthorized execution or fund loss if the underlying state commitment turns out to be fraudulent.

### Likelihood Explanation
The path requires only a legitimate cross-chain governance message from the Hyperbridge coprocessor chain reaching `HostManager.onAccept` with `OnAcceptActions.SetHostParam` — this is the normal, intended governance flow, not a malicious relayer/prover/admin action on the destination chain. Because `updateHostParamsInternal` performs no lower-bound validation on `challengePeriod` [8](#0-7) , any legitimate parameter update that shortens the challenge period (for entirely valid reasons, e.g. reducing latency) instantly and retroactively affects every previously stored, still-pending commitment on that host, not just future ones. No attacker-controlled proof forgery or compromised relayer is required — the vulnerable condition is a design gap in how the mutable parameter interacts with already-existing state.

### Recommendation
Snapshot the `challengePeriod` value at the time each `StateMachineHeight` commitment is stored (alongside `stateMachineCommitmentUpdateTime`) instead of reading the live/global value in `HandlerV2.sol`. Alternatively, enforce that `updateHostParams` can only ever increase `challengePeriod`, or require a minimum bound and a mandatory grace period before a reduced `challengePeriod` applies to previously stored commitments (mirroring the two-step "announce then commit" recommendation from the original report).

### Proof of Concept
1. Hyperbridge governance sends a `SetHostParam` request with `challengePeriod = 7 days` (long fraud window) to `EvmHost`; `updateHostParamsInternal` stores it in `_hostParams.challengePeriod` [7](#0-6) .
2. A relayer submits a consensus proof, and `HandlerV2` stores a new `StateMachineHeight` commitment with `stateMachineCommitmentUpdateTime = T` [13](#0-12) . Fishermen begin their 7-day veto window, expecting this delay to hold.
3. Before `T + 7 days`, a second governance `SetHostParam` request sets `challengePeriod = 0` (no validation blocks this) [8](#0-7) .
4. A relayer immediately calls `handlePostRequests` (or `handleGetResponses`/timeout handlers) with a proof against the height from step 2. The check `challengePeriod != 0 && challengePeriod > delay` now evaluates `0 != 0` as false, so the guard is skipped entirely and the request is dispatched [2](#0-1) , despite fishermen having had none of the originally-promised 7 days to veto the underlying state commitment.

### Citations

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

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

**File:** evm/src/core/EvmHost.sol (L633-633)
```text
        _hostParams.challengePeriod = params.challengePeriod;
```

**File:** evm/src/core/EvmHost.sol (L687-690)
```text
    function storeStateMachineCommitment(StateMachineHeight memory height, StateCommitment memory commitment)
        external
        restrict(_hostParams.handler)
    {
```

**File:** evm/src/core/HandlerV2.sol (L156-163)
```text
        for (uint256 i = 0; i < intermediatesLen; i++) {
            IntermediateState memory intermediate = intermediates[i];
            uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
            if (latestHeight != 0 && intermediate.height > latestHeight) {
                StateMachineHeight memory stateMachineHeight =
                    StateMachineHeight({stateMachineId: intermediate.stateMachineId, height: intermediate.height});
                host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
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

**File:** evm/rust/src/host_params.rs (L113-115)
```rust
		if let Some(challenge_period) = update.challenge_period {
			self.challenge_period = challenge_period;
		}
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L174-214)
```rust
#[test]
fn test_host_manager_set_host_params() {
	let mut env = TestEnv::new();
	let manager = host_manager_of(&mut env);

	let value = host_params(&mut env);
	let new_challenge_period = U256::from(5_000_000u128);

	let params = EvmHostParamsAbi {
		feeToken: value.feeToken,
		admin: value.admin,
		handler: value.handler,
		hostManager: value.hostManager,
		uniswapV2: value.uniswapV2,
		unStakingPeriod: value.unStakingPeriod,
		challengePeriod: new_challenge_period,
		consensusClient: value.consensusClient,
		stateMachines: value.stateMachines.clone(),
		hyperbridge: value.hyperbridge.to_vec().into(),
	};
	// encode_host_params prepends action byte (1 = SetHostParam)
	let body = encode_host_params(&params);

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body,
	};
	let evm_request: EvmPostRequest = post.into();

	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	env.call_as(host_addr, manager, calldata);

	let updated = host_params(&mut env);
	assert_eq!(updated.challengePeriod, new_challenge_period);
}
```

**File:** tesseract/messaging/primitives/src/lib.rs (L731-755)
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
	let mut delay = current_timestamp.saturating_sub(last_consensus_update);

	while delay <= challenge_period {
		tokio::time::sleep(challenge_period - delay).await;
		let current_timestamp = client.query_timestamp().await?;
		delay = current_timestamp.saturating_sub(last_consensus_update);
	}
	Ok(())
}
```
