### Title
`placeOrder()` never validates `order.output.beneficiary`, letting native-token fills be sent to and permanently burned at `address(0)` - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder()` accepts a fully attacker-controlled `Order` struct and only validates `order.inputs` (non-empty, non-zero amounts, no duplicate tokens). It performs **no validation whatsoever on `order.output.beneficiary`**. That value is later decoded as a raw `address` and used as the direct payment recipient in both the same-chain and cross-chain fill paths. If `order.output.beneficiary == bytes32(0)` and an output asset is the native-token sentinel (`token == address(0)`), the low-level `.call{value: amount}("")` to `address(0)` succeeds silently, permanently burning whatever native tokens the filling solver sends. This is the direct structural analog of the Connext report: an unchecked zero-value parameter (`recovery`/`agent`) supplied to a public entrypoint (`xcall`) that, in a later stage of execution, causes funds to be sent to the zero address or locked forever.

### Finding Description
`placeOrder()` in `evm/src/apps/IntentGatewayV2.sol` (lines 162-383) stamps `order.user`/`order.source`/`order.nonce`, validates `order.inputs.length != 0`, rejects zero-amount/duplicate input and output tokens, and escrows funds — but never checks `order.output.beneficiary != bytes32(0)`. [1](#0-0) 

This unchecked `beneficiary` field is later decoded and used directly as the payout address in both fill paths:

- Same-chain fill (`IntrinsicIntents._fillSameChain`): `address beneficiary = address(uint160(uint256(order.output.beneficiary)));` then, for native-token outputs, `(bool sent,) = beneficiary.call{value: beneficiaryTotal}(""); if (!sent) revert InsufficientNativeToken();` [2](#0-1) [3](#0-2) 

- Cross-chain fill (`ExtrinsicIntents._fillCrossChain`): identical pattern — `address beneficiary = address(uint160(uint256(order.output.beneficiary)));` then `(bool sent,) = beneficiary.call{value: beneficiaryTotal}("");` [4](#0-3) [5](#0-4) 

Sending value to `address(0)` via a low-level `.call` **succeeds** (`sent == true`) — there is no revert-on-zero-address guard the way OpenZeppelin's `ERC20._transfer` has for ERC-20 outputs. So while an ERC-20 output to a zero beneficiary would revert inside `safeTransfer`/`transferFrom` (self-protecting), the native-token path has no such protection and silently destroys funds. This exactly mirrors the report's core defect class: a caller-supplied address parameter at the public entrypoint (`placeOrder`, analogous to `xcall`) is never checked for the zero value, and a downstream execution stage (`fillOrder`/`_fillSameChain`/`_fillCrossChain`, analogous to `sendToRecovery()`/`forceReceiveLocal`) unconditionally uses it to move funds, resulting in funds being sent to `address(0)` and permanently lost.

Existing guards do not stop this path:
- `placeOrder` validates only `order.inputs`, never `order.output` fields. [6](#0-5) 
- `fillOrder`'s shared pre-routing checks (expiry, already-filled, solver-selection auth, array-length parity) also never touch `order.output.beneficiary`. [7](#0-6) 
- `_validateParams`, used for gateway-level `Params`, has no bearing on per-order fields like `beneficiary`. [8](#0-7) 

### Impact Explanation
This is a direct, unconditional loss-of-funds bug reachable by any unprivileged party through the standard `placeOrder`/`fillOrder` public flow — no malicious relayer, prover, or admin required. Any order requesting a native-token output (a normal, supported configuration) with `beneficiary == 0` causes the filling solver's native tokens to be irrecoverably burned the moment the order is filled, with no revert and no path to recovery. Because `fillOrder` does not surface or validate the beneficiary before moving funds, an automated solver that fills orders without independently re-validating every field of the order it did not construct will permanently lose the exact `beneficiaryTotal` amount it sends. This satisfies the "stealing or loss of funds" impact class in the bounty scope.

### Likelihood Explanation
High. `placeOrder` takes an arbitrary `Order memory` from any caller, and `beneficiary` is a plain `bytes32` field with zero validation anywhere in the placement, fill, or shared-validation code paths. No signature, proof, or privileged role is needed — an attacker (or a buggy/careless integrator) simply places a syntactically valid order with `output.beneficiary = bytes32(0)` and a native-token output asset; any solver that fills it per the normal, advertised flow loses funds automatically.

### Recommendation
Add an explicit check in `placeOrder()` (mirroring the recommended `recovery != 0` / `agent != 0` checks in the original report) that rejects orders with a zero-value output beneficiary:
```solidity
if (order.output.beneficiary == bytes32(0)) revert InvalidInput();
```
Additionally, as defense-in-depth, add the same check at the start of `fillOrder()` (or inside `_fillSameChain`/`_fillCrossChain`) before any native-token transfer is attempted, so that even orders somehow bypassing placement-time validation cannot cause a burn at fill time.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder(order, graffiti)` with:
   - `order.inputs` = a valid non-zero token/amount (e.g., 1000 USDC),
   - `order.output.assets[0] = { token: bytes32(0), amount: 1 ether }` (native-token output),
   - `order.output.beneficiary = bytes32(0)`.
   `placeOrder` accepts this without any revert, because only `order.inputs` are validated. [9](#0-8) 
2. A solver later calls `fillOrder(order, options)` with `options.outputs[0].amount = 1 ether` and `msg.value = 1 ether`, believing they are paying the order's beneficiary.
3. Inside `_fillSameChain` (same-chain) or `_fillCrossChain` (cross-chain), `beneficiary` is computed as `address(uint160(uint256(bytes32(0)))) == address(0)`, and `beneficiary.call{value: 1 ether}("")` succeeds (`sent == true`), so no revert occurs. [3](#0-2) 
4. The solver's 1 ETH is permanently lost (sent to `address(0)`), while the order is marked filled and the solver still receives the escrowed input tokens from the source side — meaning the solver's payment leg is destroyed with no recovery mechanism.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-196)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }

        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        uint256 inputsLen = order.inputs.length;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-452)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }

        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L60-60)
```text
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L101-105)
```text
            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L95-95)
```text
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L121-126)
```text
            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L532-538)
```text
    function _validateParams(Params memory p) internal view {
        if (p.host == address(0) || p.host.code.length == 0) revert InvalidInput();
        if (p.dispatcher == address(0) || p.dispatcher.code.length == 0) revert InvalidInput();
        if (p.surplusShareBps > 10_000) revert InvalidInput();
        if (p.protocolFeeBps >= 10_000) revert InvalidInput();
        if (p.priceOracle != address(0) && p.priceOracle.code.length == 0) revert InvalidInput();
    }
```
