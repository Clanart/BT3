## Analysis

Reducing the seed report to its core broken invariant: **a governance-configurable timing/window parameter can be set to `0`, and downstream code treats `0` as "requirement disabled" rather than enforcing a hard-coded floor, silently defeating the security mechanism the parameter exists to enforce** — while validation of other, less critical parameters (in the seed, nothing at all; here, `unStakingPeriod`) *does* get a floor check, exposing the asymmetry as an oversight rather than a deliberate design choice.

The direct local analog is Hyperbridge's `challengePeriod`, which gates how long a state commitment must sit before it can be used to justify requests, responses, or timeouts (the window during which fishermen call `veto_state_commitment` to reject fraudulent L2 state). Both the EVM handler and the Substrate core handler special-case `challengePeriod == 0` as "no delay required," and `EvmHost.updateHostParamsInternal` enforces a floor only for `unStakingPeriod` (`if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();`) but has **no equivalent lower-bound check for `challengePeriod`**. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
`challengePeriod` can be set to 0 with no enforced floor, collapsing the fisherman veto window and letting unelapsed/fraudulent state commitments be trusted immediately - (File: `evm/src/core/EvmHost.sol`, `evm/src/core/HandlerV2.sol`, `modules/ismp/core/src/handlers.rs`)

### Summary
`HostParams.challengePeriod` is meant to be a *minimum* waiting period after a state commitment is stored, giving fishermen time to detect and veto a fraudulent L2 state before any cross-chain message relying on it is processed. Both the EVM message handler and the Substrate ISMP core explicitly treat `challengePeriod == 0` as "no delay required" instead of rejecting it as an invalid configuration, and `updateHostParamsInternal` validates a floor only for the unrelated `unStakingPeriod`, leaving `challengePeriod` completely unbounded (including `0`).

### Finding Description
`HandlerV2.handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts` all gate on:
```solidity
uint256 challengePeriod = host.challengePeriod();
if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
``` [4](#0-3) 

The `!= 0` check means a `challengePeriod` of `0` unconditionally satisfies the guard regardless of `delay`, i.e. the "wait for the challenge period" invariant is not merely weak but entirely disabled.

The identical pattern exists on the Substrate side:
```rust
Ok(delay_period.as_secs() == 0 || current_timestamp.saturating_sub(update_time) > delay_period)
``` [5](#0-4) 

`updateHostParamsInternal`, the function that applies new `HostParams` (reachable via cross-chain governance through `HostManager.onAccept` → `IHostManager(_params.host).updateHostParams`, and on `TestnetHost` also via the configured `admin`), validates several fields to "prevent the host from getting bricked" — including a hard floor of `1 days` for `unStakingPeriod` — but performs **no validation at all** on `challengePeriod`:
```solidity
if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();
...
_hostParams.challengePeriod = params.challengePeriod; // no lower-bound check
``` [6](#0-5) 

This is structurally identical to the seed bug: a security-critical time window (`auctionWindow` in the seed, `challengePeriod` here) can be driven to `0` through an authorized configuration path, and none of the consuming code paths reject `0` as invalid — they either can't be satisfied (seed) or, worse here, are trivially satisfied, defeating the very protection the window exists to provide. The fisherman documentation confirms the design intent that `challengePeriod` is the sole window in which fraudulent L2 state can be caught before relayers and applications begin trusting it: [7](#0-6) [8](#0-7) 

### Impact Explanation
If `challengePeriod` is `0` (either through an oversight in a `HostManager`-driven param update that carries the field unset/default, or an intentional misconfiguration that isn't rejected by validation), state commitments become immediately usable to process POST requests, GET responses, and timeouts the instant they are stored — with zero time for fishermen to observe and veto a fraudulent commitment submitted by a faulty or compromised consensus client. This directly violates the stated protocol invariant that "consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted," enabling false state acceptance and, downstream, unauthorized execution of cross-chain requests/responses and potential fund loss for any application relying on Hyperbridge's proof verification.

### Likelihood Explanation
The vulnerability is a missing input-validation guard, not a hypothetical edge case: `updateHostParamsInternal` demonstrably validates other fields (`hostManager`, `handler`, `consensusClient`, `hyperbridge`, `stateMachines`, `unStakingPeriod`) but omits any check on `challengePeriod`, showing the floor check was simply forgotten for this field while being applied elsewhere in the same function. Any future `HostParams` update (including a partial/default-valued struct) that leaves `challengePeriod` at `0` silently disables the guard on all four handler entrypoints, with no revert or warning anywhere in the update path.

### Recommendation
Add an explicit lower-bound check on `challengePeriod` in `updateHostParamsInternal` (e.g., `if (params.challengePeriod < MIN_CHALLENGE_PERIOD) revert InvalidChallengePeriod();`), and remove the `challengePeriod != 0` bypass in `HandlerV2.sol`'s four handler functions and the equivalent `delay_period.as_secs() == 0` bypass in `modules/ismp/core/src/handlers.rs::verify_delay_passed`, so that `0` is rejected as a configuration value rather than treated as "no delay required."

### Proof of Concept
1. Cross-chain governance (via `HostManager.onAccept` → `EvmHost.updateHostParams`) submits a `HostParams` update where `challengePeriod = 0` (e.g., from an `EvmHostParam::default()` value as seen used in test setup) — `evm/src/core/EvmHost.sol` accepts it since no floor is enforced. [9](#0-8) 
2. A relayer submits a `PostRequestMessage` whose `proof.height` corresponds to a state commitment stored in the same block (`delay == 0`).
3. In `HandlerV2.handlePostRequests`, `challengePeriod != 0` evaluates to `false`, so `ChallengePeriodNotElapsed()` never reverts regardless of `delay`. [10](#0-9) 
4. The request is dispatched to the destination module immediately, before any fisherman has had a chance to call `veto_state_commitment` on the corresponding state commitment, allowing a fraudulent or premature state commitment to be acted upon as if it were finalized. [11](#0-10)

### Citations

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

**File:** evm/src/core/EvmHost.sol (L607-636)
```text
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

**File:** docs/content/developers/explore/fishermen.mdx (L8-20)
```text
Every collator on Hyperbridge doubles as a fisherman. The fisherman task runs inside the collator binary and watches each connected L2 across multiple independent RPC providers. When those providers reach a supermajority agreeing on an L2 state that contradicts what Hyperbridge is about to commit to its state trie, the fisherman submits a `veto_state_commitment` extrinsic signed by the collator's AURA key. This veto prevents the fraudulent commitment from being finalised on Hyperbridge, protecting every application that relies on cross-chain state proofs.

Collators are compensated 2 `$BRIDGE` per block produced. This reward covers both block production and the ongoing cost of operating a high-quality fisherman setup — running premium RPC endpoints across multiple independent providers is part of the job, and the block reward is sized to make that sustainable.

## What a Fisherman Can Do

A fisherman holding a seat in the active collator set can:

- **Veto a state commitment** by calling `pallet-fishermen.veto_state_commitment` with a specific `StateMachineHeight`. The veto is accepted without requiring a cryptographic proof — the fisherman's inclusion in the collator set is the trust anchor.
- **Block cross-chain message delivery** for the vetoed height. Because a state commitment is an accumulator, messages from a vetoed height will still be included in a later commitment once the correct state is finalised. A veto delays processing; it cannot censor a specific message permanently.
- **Prevent a compromised or faulty consensus client** from anchoring fraudulent L2 state on Hyperbridge, which would otherwise let attackers fabricate state proofs and drain applications of funds.

A fisherman cannot selectively censor individual messages. The veto operates at the state commitment level — either the entire state at a given height is accepted or it is rejected.
```

**File:** docs/content/protocol/ismp/consensus.mdx (L215-216)
```text
A `StateMachineUpdated` event is emitted to notify network participants (both relayers and fishermen) of some newly available `StateCommitment`s for a given state machine. Relayers will wait for the configured `challenge_period` before attempting to transmit new requests & responses. While fishermen will check if these pending `StateCommitment`s describe valid states on the counterparty network. If the `challenge_period` elapses without any fraud proofs being presented, we can safely conclude that the provided `StateCommitment`s are indeed canonical.

```

**File:** tesseract/messaging/integration-test/src/lib.rs (L162-188)
```rust
/// A function to set host params when the network is spawned for ismp messages execution to work
async fn set_host_params(
	chain_sub_client: SubstrateClient<Hyperbridge>,
) -> Result<(), anyhow::Error> {
	// Substrate host params have been removed; only EVM host params remain. The
	// destinations below are substrate parachains, so we register a default
	// `EvmHostParam` entry purely to satisfy the storage shape — substrate-to-
	// substrate messaging does not consult the params during dispatch.
	if chain_sub_client.state_machine_id().state_id == StateMachine::Kusama(2000) {
		chain_sub_client
			.clone()
			.set_host_params(BTreeMap::from([(
				StateMachine::Kusama(2001),
				HostParam::EvmHostParam(EvmHostParam::default()),
			)]))
			.await?;
	} else {
		chain_sub_client
			.clone()
			.set_host_params(BTreeMap::from([(
				StateMachine::Kusama(2000),
				HostParam::EvmHostParam(EvmHostParam::default()),
			)]))
			.await?;
	}
	Ok(())
}
```

**File:** modules/pallets/fishermen/src/lib.rs (L165-193)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight((<T as frame_system::Config>::DbWeight::get().reads_writes(1, 2), Pays::No))]
		pub fn veto_state_commitment(
			origin: OriginFor<T>,
			height: StateMachineHeight,
		) -> DispatchResult {
			let account = ensure_signed(origin)?;
			ensure!(T::IsCollator::contains(&account), Error::<T>::UnauthorizedAction);

			let ismp_host = <T as Config>::IsmpHost::default();
			let commitment =
				ismp_host.state_machine_commitment(height).map_err(|_| Error::<T>::VetoFailed)?;
			ismp_host.delete_state_commitment(height).map_err(|_| Error::<T>::VetoFailed)?;

			Self::deposit_event(Event::StateCommitmentVetoed {
				height,
				commitment,
				fisherman: account.clone(),
			});
			pallet_ismp::Pallet::<T>::deposit_event(
				ismp::events::Event::StateCommitmentVetoed(StateCommitmentVetoed {
					height,
					fisherman: account.as_ref().to_vec(),
				})
				.into(),
			);

			Ok(())
		}
```
