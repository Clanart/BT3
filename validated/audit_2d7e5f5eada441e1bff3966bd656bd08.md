## Finding



### Title
Duplicate-output-token guard silently dropped in TRON's `IntentGatewayV2.placeOrder` rewrite (EVM-version-incompatible transient storage) — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The canonical EVM `IntentGatewayV2` rejects orders whose `output.assets` array contains duplicate token addresses, using a Cancun transient-storage (`tstore`/`tload`) check inside `placeOrder`: [1](#0-0) 

Because TRON's TVM does not support transient storage and the TRON build targets `evmVersion: "istanbul"`: [2](#0-1) 

the team maintains a separate, hand-written TRON port of the contract that does not inherit `ReentrancyGuardTransient` and does not reproduce the transient-storage duplicate-token check at all: [3](#0-2) [4](#0-3) 

### Finding Description
`evm/src/apps/IntentGatewayV2.sol`'s `placeOrder` explicitly guards against duplicate `output.assets[i].token` entries before escrowing inputs, because Hyperbridge's intent-settlement flow (fill accounting, surplus splitting, escrow release) assumes each output token appears at most once per order: [1](#0-0) 

The TRON variant of `IntentGatewayV2` was created as a workaround for the EVM-version incompatibility (TVM/istanbul lacks EIP-1153 `TSTORE`/`TLOAD`), the same class of bug flagged in the external report (Shanghai `PUSH0` incompatibility forcing a downgrade). However, unlike the Ambire fix — which only changed the compiler target and re-verified equivalence — the Hyperbridge TRON port is a full manual re-implementation that dropped the duplicate-output-token invariant instead of replacing it with an equivalent storage-based check. `placeOrder` in the TRON contract computes the order commitment and escrows `order.inputs` with no validation at all on `order.output.assets` uniqueness: [5](#0-4) 

This means a user can place an order on TRON whose `output.assets` array repeats the same token multiple times with different (or same) amounts — something the reference/audited EVM implementation explicitly prevents by design. Because commitment hashing (`keccak256(abi.encode(order))`) treats the array as ordered data rather than deduplicating it, downstream fill/settlement logic that assumes unique per-token output amounts (fill validation, solver payout accounting, surplus computation) can be driven with a malformed order shape that the reference implementation was hardened against.

### Impact Explanation
This breaks the "request/response paths must bind commitment uniqueness" and "bridged assets/order escrow must move exactly once and only to the rightful beneficiary and amount" invariants for the TRON deployment specifically. Since the guard exists in the canonical contract precisely to prevent duplicate-output-token orders from being escrowed, its complete absence on TRON is a genuine functional divergence between chains introduced by the EVM-incompatibility workaround — not merely a cosmetic difference. An attacker on TRON can place orders that the security model assumes are unreachable, and any solver-side or on-chain fill logic that relies on "each output token appears once" can be pushed into an unvalidated state that the reference implementation was specifically designed to reject.

### Likelihood Explanation
Likelihood is high for reachability: `placeOrder` is a fully public, unprivileged entry point requiring no special role, relayer, or governance action — any user can call it directly with a crafted `Order` containing duplicate `output.assets[i].token` entries. The divergence is deterministic and always present in the TRON deployment (not a race condition or a rare edge case); it exists in-code every time the TRON contract is used, as a direct byproduct of the EVM-version-driven contract rewrite.

### Recommendation
Add the equivalent duplicate-output-token validation to `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`, using a standard storage mapping (since transient storage is unavailable on TVM/istanbul) that is set and cleared within the same call, mirroring the invariant enforced in `evm/src/apps/IntentGatewayV2.sol`. More generally, any chain-specific rewrite necessitated by EVM/opcode incompatibilities (as flagged in the external report) should be diffed against the reference implementation for dropped invariants as part of the review/audit process, not just for compiler-target compatibility.

### Proof of Concept
1. On TRON, call `IntentGatewayV2.placeOrder(order, graffiti)` with `order.output.assets = [{token: USDC, amount: X}, {token: USDC, amount: Y}]` (same token repeated).
2. Compare to the canonical EVM chain (e.g., Ethereum/Base): the identical call reverts with `InvalidInput` due to the `tload`/`tstore` duplicate check: [6](#0-5) 
3. On TRON, the call succeeds because no equivalent check exists in `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`: [5](#0-4) 
4. This demonstrates a security-relevant invariant present on all "canonical" EVM deployments that is silently absent from the TRON deployment as a direct consequence of the EVM-version incompatibility (transient storage unsupported under `evmVersion: "istanbul"`) that necessitated the rewrite.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-180)
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
```

**File:** evm/tron/tronbox.js (L86-98)
```javascript
    compilers: {
        solc: {
            version: "0.8.25",
            settings: {
                optimizer: {
                    enabled: true,
                    runs: 200,
                },
                evmVersion: "istanbul",
                viaIR: true,
            },
        },
    },
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-61)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;

    /**
     * @dev EIP-712 type hash for SelectSolver message
     */
    bytes32 public constant SELECT_SOLVER_TYPEHASH = keccak256("SelectSolver(bytes32 commitment,address solver)");
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-463)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }

        // escrow tokens
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```
