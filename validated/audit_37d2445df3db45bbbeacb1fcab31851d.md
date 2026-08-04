### Title
Governance actions in IntentGatewayV2 / ExtrinsicIntents `onAccept` authenticate only the source chain, not the sending module - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol` and `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
The external report's core broken invariant is: a component was given broad, unscoped trust ("access to root entropy for all Ethereum keys") instead of a narrowly-authenticated capability tied to a specific, verifiable identity. The Hyperbridge analog is in `IntentGatewayV2.onAccept` (`evm/tron/contracts/apps/IntentGatewayV2.sol:620-674`) and its sibling `ExtrinsicIntents.onAccept` (`evm/src/apps/intentsv2/ExtrinsicIntents.sol:289-309`): the privileged branch that executes `NewDeployment`, `UpdateParams`, `SweepDust`, and (in `ExtrinsicIntents`) `UpgradeContract` authenticates the incoming request only by checking that `incoming.request.source` equals the Hyperbridge chain id — it never checks `incoming.request.from`, i.e. it never verifies which specific module/pallet on the Hyperbridge coprocessor actually dispatched the message.

### Finding Description
Contrast the two authentication paths inside the same `onAccept` function:

- Escrow release/refund path calls `_authenticate`/`authenticate`, which validates `request.from` against the registered per-chain gateway instance address: [1](#0-0) 

- The governance path checks only the chain identifier, never `request.from`: [2](#0-1) [3](#0-2) 

On the Substrate side, the intended sender of these governance actions is `pallet-intents-coprocessor`, which always sets `from: PALLET_INTENTS_ID.to_vec()`: [4](#0-3) 

But `PostRequest.from` is fully caller-supplied at the `dispatch_request` layer — `pallet-ismp`'s dispatcher simply forwards whatever `from` bytes the calling module passes, with no cryptographic binding to a specific pallet/module identity: [5](#0-4) 

Because the EVM-side `onAccept` governance branch validates only `source` (the entire Hyperbridge state machine) and never `from` (the specific module on that chain), **any** ISMP module/pallet on the Hyperbridge coprocessor that is capable of dispatching a POST request to the `IntentGatewayV2`/`ExtrinsicIntents` contract address is treated as fully authorized to: register a new gateway `Deployment` (redirecting escrow routing), overwrite `_params`/`_destinationProtocolFees` via `UpdateParams`, drain accumulated dust to an arbitrary `beneficiary` via `SweepDust`, or (in `ExtrinsicIntents`) upgrade the ERC-1967 proxy to an arbitrary implementation with arbitrary init calldata via `UpgradeContract`. This is exactly the MetaMask-Snap analog: the "provider" (host chain identity) is trusted wholesale instead of confirming the specific, scoped caller (module identity), collapsing the intended narrow trust boundary (`PALLET_INTENTS_ID` only) into a broad one (any module on Hyperbridge).

### Impact Explanation
This directly matches the bounty's admitted impacts: unauthorized transaction/execution, logic attack, and false-state acceptance leading to loss of funds. `SweepDust` can drain arbitrary ERC20/native token balances held by the contract to an attacker-chosen address; `UpgradeContract` grants full contract takeover (arbitrary code execution over all escrowed user funds); `NewDeployment`/`UpdateParams` can redirect `RedeemEscrow`/`RefundEscrow` authentication (`_instance(...)`/`instance(...)`) to an attacker-controlled address, letting an attacker subsequently drain all escrowed order funds through the "legitimate" `RedeemEscrow`/`RefundEscrow` path.

### Likelihood Explanation
The check omission is unconditional and independent of proof validity — `onAccept` is invoked by the trusted `onlyHost` after normal ISMP proof/membership verification has already succeeded (that part is not bypassed), but the *authorization* logic downstream of that point conflates "message came from chain X" with "message came from privileged module Y on chain X." Any current or future ISMP module/pallet integrated into the Hyperbridge coprocessor's router (which is explicitly designed to be extensible, per `IsmpRouter::module_for_id`) that can dispatch a POST to this destination with attacker-influenced body content satisfies the check, without requiring a malicious relayer, prover, or admin — the flaw is a missing application-level access control, not a consensus/proof bypass.

### Recommendation
In both `IntentGatewayV2.onAccept` and `ExtrinsicIntents.onAccept`, replace the source-chain-only check with an explicit check that `incoming.request.from` equals the specific, registered governance module identifier (e.g., `PALLET_INTENTS_ID`) in addition to `incoming.request.source == hyperbridge`, mirroring the `_authenticate`/`authenticate` pattern already used for the escrow-release path.

### Proof of Concept
1. A user (or any existing/future ISMP module on the Hyperbridge coprocessor) constructs and dispatches a `PostRequest` where `source = HostStateMachine::get()` (Hyperbridge's own chain id, trivially satisfied since Hyperbridge is always the source when dispatching from itself), `to = <IntentGatewayV2/ExtrinsicIntents address>`, and `from = <any module id other than PALLET_INTENTS_ID>` (unchecked), with `body[0] = RequestKind.SweepDust` and `body[1:] = abi.encode(SweepDust{ outputs: [...], beneficiary: attacker })`.
2. Once relayed and delivered, `onlyHost` passes (legitimate host, legitimate proof of the request's inclusion), and the code reaches:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
// kind == RequestKind.SweepDust -> executes unconditionally, no check on incoming.request.from
``` [6](#0-5) 
3. `_sweepDust`/`SweepDust` handling transfers the contract's accumulated token/native balances to `attacker`-controlled `beneficiary`, with no verification that the dispatching module was the intended `PALLET_INTENTS_ID` governance pallet.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L297-309)
```text
        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-674)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;

            // Update destination-specific protocol fees if provided
            for (uint256 i; i < update.destinationFees.length;) {
                bytes32 stateMachineId = update.destinationFees[i].stateMachineId;
                uint256 feeBps = update.destinationFees[i].destinationFeeBps;
                _destinationProtocolFees[stateMachineId] = feeBps;

                unchecked {
                    ++i;
                }
                emit DestinationProtocolFeeUpdated(stateMachineId, feeBps);
            }
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L924-946)
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

			let dispatch_request = DispatchRequest::Post(post);

			// Create fee metadata with zero fee (no actual fee payment for governance operations)
			let dispatcher_fee = FeeMetadata { payer: [0u8; 32].into(), fee: Zero::zero() };

			// Dispatch via ISMP
			let commitment = dispatcher
				.dispatch_request(dispatch_request, dispatcher_fee)
				.map_err(|_| Error::<T>::DispatchFailed)?;
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-146)
```rust
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
```
