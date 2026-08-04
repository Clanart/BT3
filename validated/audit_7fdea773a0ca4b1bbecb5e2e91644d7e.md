## Title
Missing `request.from` (sender identity) validation on Hyperbridge-privileged `onAccept` actions in `IntentGatewayV2`/`ExtrinsicIntents` — governance actions (UpgradeContract, SweepDust, UpdateParams, NewDeployment) are authenticated only by *chain id*, not by *module identity* - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
Just as the external report flags a `NotarizedTransaction` struct whose validator checks some fields (`id`, `status`, `transaction_hash`...) but silently skips others (`ThresholdKey`, etc.), Hyperbridge's `IntentGatewayV2` governance dispatch path validates only one member of the incoming ISMP message (`request.source` — the origin chain id) while never validating the other member that actually identifies *who* on that chain sent it (`request.from`). The Hyperbridge Pivots explicitly require binding both "chain id" **and** "module/app identity" for request paths; this handler binds only the former.

### Finding Description
`onAccept` in `evm/src/apps/intentsv2/ExtrinsicIntents.sol` branches on the first byte of the request body (`RequestKind`): [1](#0-0) 

For the two beneficiary-facing kinds (`RedeemEscrow`/`RefundEscrow`) it calls `_authenticate(incoming.request)`, which (per the doc comment) checks the message came from "the registered gateway instance for the source chain" — i.e. it validates **both** `request.source` (chain) and `request.from` (module address) against `_instances[stateMachineId]`.

But for the four *privileged* kinds — `NewDeployment`, `UpdateParams`, `SweepDust`, `UpgradeContract` — the only guard is:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
```
This checks only that the message's `source` field equals the Hyperbridge state-machine id. It never checks `incoming.request.from` against any expected governance module/pallet address on Hyperbridge. The identical pattern exists in the parallel implementation: [2](#0-1) 

`request.source` is authenticated by the ISMP consensus/state-proof pipeline (it truly proves the message was committed by the Hyperbridge state machine), but `request.from` is attacker-controlled data set at dispatch time by *whichever account or pallet on Hyperbridge* called `dispatch()` — ISMP's dispatch primitive lets any caller set `from` to their own account/address. Nothing in the ISMP core (`handlers/request.rs`, `EvmHost.dispatchIncoming`) constrains `from` to a privileged sender; that binding is left entirely to the receiving application, which is exactly what `_authenticate()` does correctly for `RedeemEscrow`/`RefundEscrow` but what the governance branch omits.

Consequently, any account or pallet capable of triggering an ISMP POST dispatch from the Hyperbridge state machine — with `to` set to a deployed `IntentGatewayV2` contract address on any connected EVM chain and body-byte-0 set to `UpgradeContract`/`SweepDust`/`UpdateParams`/`NewDeployment` — passes the sole `source == hyperbridge` check and reaches:
- `UpgradeContract` → `ERC1967Utils.upgradeToAndCall(newImpl, initData)` — full proxy takeover, arbitrary code execution over all escrowed user funds.
- `SweepDust` → transfers arbitrary ERC20/native "dust" balances to an attacker-chosen beneficiary.
- `UpdateParams` → rewrites `protocolFeeBps`, `surplusShareBps`, `dispatcher`, `host`, and per-destination fee overrides.
- `NewDeployment` → registers an attacker-chosen address as the trusted "instance" for an arbitrary state machine id, which is exactly the address `_authenticate()` trusts for future `RedeemEscrow`/`RefundEscrow` withdrawals — enabling the attacker to subsequently drain all escrowed order inputs on that destination via forged withdrawal messages.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds," "unauthorized transaction or execution," and "cross-chain admin ... effects reachable through ... wrong module bindings" categories. A successful exploit yields complete compromise of the `IntentGatewayV2` deployment: contract upgrade (arbitrary code), dust/fund theft, fee-parameter hijack, and forged trusted-instance registration that cascades into draining all escrowed cross-chain intents.

### Likelihood Explanation
Exploitability hinges only on whether an unprivileged party can cause an ISMP POST to be dispatched *from* the Hyperbridge state machine with an attacker-chosen `from`. `from` is set by the calling account/pallet at dispatch time and is not restricted by the core ISMP dispatch path — the receiving app is solely responsible for validating it, and here that validation is missing for the privileged branch while present for the ordinary branch. This is a straightforward "check one struct field, forget the other" defect of exactly the class the seed report describes, requiring no malicious relayer, prover, or governance actor — only an account able to route a POST to the target contract from Hyperbridge.

### Recommendation
In `onAccept` (both `ExtrinsicIntents.sol` and the tron `IntentGatewayV2.sol`), require that privileged `RequestKind`s additionally validate `incoming.request.from` against a stored, governance-set trusted sender address (e.g., a dedicated governance module id on Hyperbridge), mirroring the `_authenticate()` pattern already used for `RedeemEscrow`/`RefundEscrow`. Do not rely on `source` chain-id equality alone to authorize state-mutating or upgrade actions.

### Proof of Concept
1. Deploy/observe an `IntentGatewayV2` instance on chain `X`, with `IDispatcher(host()).hyperbridge()` returning Hyperbridge's state machine id `H`.
2. From Hyperbridge (`H`), dispatch (via any account/pallet capable of calling the ISMP dispatcher with a self-chosen `from`) a POST request:
   - `to = address(intentGatewayOnX)`
   - `body = abi.encodePacked(uint8(RequestKind.UpgradeContract), abi.encode(maliciousImpl, initCalldata))`
3. A relayer delivers this message through the standard `handlePostRequests` proof path on chain `X` (this succeeds because the request genuinely was committed by Hyperbridge's state machine — the proof itself is valid).
4. `onAccept` checks `keccak256(incoming.request.source) == keccak256(hyperbridge)` → true, and reaches `ERC1967Utils.upgradeToAndCall(maliciousImpl, initCalldata)` without ever checking who on Hyperbridge (`incoming.request.from`) actually sent it.
5. The attacker's implementation now controls the `IntentGatewayV2` proxy and all escrowed funds. [3](#0-2)

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-309)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-674)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }

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
