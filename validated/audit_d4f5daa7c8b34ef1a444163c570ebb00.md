### Title
Cross-chain fill hardcodes the escrow-redemption beneficiary to `msg.sender`, permanently locking escrowed native tokens if the solver's address cannot receive plain ETH on the source chain - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
The LiFi report's core defect is that a "recovery"/fallback beneficiary address is hardcoded to `msg.sender` instead of being an explicit, chosen parameter, so if that address is a smart contract that cannot receive assets on the settlement chain, funds get stuck. The IntentGatewayV2 cross-chain fill path repeats this exact pattern: the `RedeemEscrow` message's beneficiary is hardcoded to the filling solver's own `msg.sender` address on the destination chain, and that same raw address is used as the ETH-transfer target when the message is executed on the source chain.

### Finding Description
When a solver fills a cross-chain order, `_fillCrossChain` builds the settlement message with the beneficiary fixed to the caller's own address: [1](#0-0) 

That `WithdrawalRequest.beneficiary` is never supplied or chosen by the solver as a separate "receive address" argument — it is derived purely from `msg.sender` at fill time. When the message lands on the source chain, `onAccept` authenticates it and calls `_withdraw`, which for native-token (`address(0)`) escrow performs a raw low-level call to that beneficiary and reverts the entire transaction if the call fails: [2](#0-1) 

The same fixed-address pattern appears in the onAccept dispatch handling for `RedeemEscrow`/`RefundEscrow`: [3](#0-2) 

Because Hyperbridge documents a `SolverAccount` smart-contract wallet pattern (ERC-4337 + EIP-7702 + ERC-7821 batch execution) that solvers delegate their EOA to for filling orders, `msg.sender` at fill time on the destination chain is frequently a smart-contract account rather than a plain EOA: [4](#0-3) 

If that same address is a contract on the source chain (e.g. via the same EIP-7702 delegation designation, which is address-portable across chains) and its implementation has no unconditional payable `receive()`/`fallback()` — which is normal for account-abstraction contracts that only accept calls routed through specific entry points/selectors — the native-token `.call{value: amount}("")` in `_withdraw` will revert. This reverts the whole `onAccept` execution, so the ISMP message delivery transaction itself fails.

### Impact Explanation
Unlike the `SweepDust`/`_sweepDust` governance path or the `RefundEscrow` path (both of which correctly bind the beneficiary to `order.user`, an address the user explicitly controls and chose at order-placement time — see `_cancelFromDest` binding `beneficiary: order.user`), the `RedeemEscrow` path binds the beneficiary to an address the protocol never validates can actually receive assets on the *other* chain. Once the message is dispatched with this immutable beneficiary, there is no way to change it: every relayer redelivery attempt hits the identical revert. The order is already marked filled on the destination chain (`_filled[commitment] = msg.sender` set at the top of `fillOrder`), which blocks the user's own cancel-from-destination and cancel-from-source recovery paths (a `GET` proof of the destination `_filled` slot will show it non-empty, reverting cancellation with `Filled()`). The result: the user's escrowed native-token input is permanently stranded in the source-chain gateway contract, unreachable by the solver (can't be paid out) and unreachable by the user (order already counted as filled) — a direct loss/lock of bridged funds.

### Likelihood Explanation
This requires no malicious relayer, prover, or governance actor — a completely ordinary, protocol-encouraged solver flow (using the documented `SolverAccount` smart-account pattern, or any custom solver contract without a bare-ETH-accepting fallback) triggers it deterministically whenever the order's input asset is the native token. No proof forgery or wrong-chain trickery is needed; the beneficiary is simply an address type the contract never checks for the ability to receive value on the settlement chain.

### Recommendation
Do not hardcode the `RedeemEscrow` beneficiary to `msg.sender`. Let the solver supply an explicit, separate settlement/recovery address as part of `FillOptions` (analogous to how `order.user` is used for refunds), and/or wrap the native-token transfer in `_withdraw` with a bounded-gas call plus a pull-payment fallback (e.g., credit an internal balance the intended beneficiary can withdraw later) so a non-payable beneficiary cannot permanently brick settlement of an already-filled order.

### Proof of Concept
1. A solver deploys/uses the documented `SolverAccount` (or any smart-contract wallet) at address `S` on both the destination chain (e.g. Arbitrum) and, incidentally, the source chain (e.g. via the same EIP-7702 delegation designation, so `S` has code on both chains) that does not implement an unconditional payable `receive()`/`fallback()`.
2. Order specifies a native-token (`address(0)`) input, escrowed on the source chain by the user via `placeOrder`.
3. Solver calls `fillOrder` from `S` on the destination chain; `_fillCrossChain` dispatches a `RedeemEscrow` `WithdrawalRequest` with `beneficiary = bytes32(uint256(uint160(S)))` (evm/src/apps/intentsv2/ExtrinsicIntents.sol:144).
4. The message is delivered to the source chain; `onAccept` calls `_withdraw(body, false, true)`.
5. `_withdraw` executes `S.call{value: amount}("")` for the native-token line item; since `S` on the source chain rejects the plain ETH transfer, the call returns `false`, and `_withdraw` reverts with `InsufficientNativeToken()` (evm/src/apps/intentsv2/IntentsBase.sol:404-406).
6. Every subsequent relayer delivery attempt reverts identically. The order is already marked `_filled` on the destination chain, so the user's cancel-from-source GET-proof path also reverts with `Filled()`. The escrowed native tokens remain stuck in the source-chain `IntentGatewayV2`/`ExtrinsicIntents` contract indefinitely.

Note: I was not able to fully trace whether an alternate manual-recovery entrypoint (e.g. a governance sweep specific to stuck per-commitment escrow, distinct from the general `SweepDust` dust-only sweep) exists elsewhere in the codebase; if such a path exists it would mitigate permanence but does not change that the funds are inaccessible to both the rightful solver and the user through the normal protocol flow.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-147)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L112-120)
```text
## Solver Selection

When `solverSelection` is enabled in the intent gateway parameters, orders are protected from unauthorized fills. At order placement, the user specifies a `session` key — a temporary keypair generated for this order. Only a solver explicitly authorized by the session key can fill the order.

### `SolverAccount`

The SolverAccount is a smart account designed for solvers that combines [ERC-4337](https://eips.ethereum.org/EIPS/eip-4337) (account abstraction), [EIP-7702](https://eips.ethereum.org/EIPS/eip-7702), and [ERC-7821](https://eips.ethereum.org/EIPS/eip-7821) (batch execution) to batch `gateway.select(...)` and `gateway.fillOrder(...)` into a single atomic UserOperation. Solvers delegate their EOA to the SolverAccount via EIP-7702 and submit bundled operations through the ERC-4337 EntryPoint.

`SolverAccount.validateUserOp` accepts two signature formats, discriminated by length: a standard 65-byte ECDSA signature over the `userOpHash` for regular account operations (delegation no-ops, approvals, treasury batches), and the 162-byte intent-selection payload `abi.encodePacked(commitment, solverSignature, sessionSignature)` for fills. UserOperations whose calldata contains a `fillOrder` call to the gateway are refused on the standard path. This guard exists because bids are public on Hyperbridge and embed a valid 65-byte solver signature over the `userOpHash` — without it, anyone could strip the commitment and session signature from a bid and submit the bare operation: the fill would revert (no selection is staged during validation), but it would still consume the bid's nonce and gr ... (truncated)
```
