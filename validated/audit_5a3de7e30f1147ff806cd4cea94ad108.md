## Analysis

The seed report's core broken invariant is: **code assumes a token exposes a specific external function and that a call to it reliably signals real value movement; when that assumption breaks, funds get silently stuck/lost with no fallback.**

The strongest local analog is in the Tron-targeted `IntentGatewayV2.sol`, where escrow release (`withdraw`) and dust-sweep (`onAccept`/`SweepDust`) use **raw low-level `.call()` with only the boolean `success` checked**, instead of `SafeERC20.safeTransfer` (which the same contract already imports and uses on the deposit path). [1](#0-0) [2](#0-1) 

Deposits, by contrast, go through `SafeERC20.safeTransferFrom`, which internally requires the token target to have code and validates return data: [3](#0-2) 

A raw `address.call(...)` to an address with **no contract code** trivially returns `success == true` with empty return data — this is baseline EVM/TVM behavior, not a bug in the token. Because `withdraw()` only checks `success`, and never checks `token.code.length` nor decodes/validates the returned boolean, a token that had code at deposit time but has no code at withdrawal time will make `withdraw()` believe the transfer succeeded, while zero value actually moves.

### Title
Escrow release trusts a no-op low-level `call` as proof of token transfer, enabling silent fund loss — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`withdraw()` (used for both `RedeemEscrow` and `RefundEscrow`) and the `SweepDust` handler in `onAccept()` release escrowed ERC20 tokens using a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))`, checking only the boolean `success` return of the call — not that the token contract has code, and not that any returned boolean data is `true`. On Tron's TVM, `SELFDESTRUCT` clears contract code even across separate transactions (unlike post-Cancun Ethereum semantics), so a token contract that had code and received a real deposit via `safeTransferFrom` at `placeOrder` time can be self-destructed by its creator before the corresponding `RedeemEscrow`/`RefundEscrow` message is delivered. When `withdraw()` then calls the now-codeless token address, the call trivially "succeeds" with no state change, the function decrements `_orders[...]` and emits `EscrowReleased`/`DustSwept` as if funds moved, while the beneficiary receives nothing.

### Finding Description
- Deposit path (`placeOrder`) uses `IERC20(token).safeTransferFrom(...)`, which requires the token to be a contract and validates return data. [3](#0-2) 
- Release path (`withdraw`) uses a bare `token.call(...)`, checking only `success`: [4](#0-3) 
- Same pattern for accumulated transaction fees: [5](#0-4) 
- And for dust sweeping via governance-originated `SweepDust` requests: [6](#0-5) 

`withdraw()` is reached from `onAccept()`, which is only gated by `onlyHost` + `authenticate()` (verifying the message came from the counterpart `IntentGatewayV2` instance on the order's source/destination chain) — it does not, and cannot, verify that the escrowed token still has code at settlement time: [7](#0-6) 

Because the order's `token` field is chosen by the order's creator (an unprivileged user) at `placeOrder` time, and nothing prevents that address from being a contract the creator controls and can later self-destruct, this is directly attacker-reachable without needing a malicious relayer, prover, or admin.

### Impact Explanation
An order creator can:
1. Deploy a throwaway ERC20-compliant token contract they control.
2. Place a cross-chain order escrowing that token as `order.inputs`.
3. Let a solver fill the order, paying real output assets to the beneficiary (the order creator) on the destination chain — this happens immediately in `_fillCrossChain`/`fillOrder` and is irreversible.
4. Before the `RedeemEscrow` message settles on the source chain, self-destruct the input token contract.
5. When `withdraw()` runs, the low-level `.call()` to the now-codeless token address returns `success = true` trivially; `_orders[commitment][token]` is decremented and `EscrowReleased` fires, but the solver (rightful beneficiary) receives zero tokens.

This is a direct theft of the solver's real output assets funded by a phantom escrow release — "stealing or loss of funds" via false acceptance of a transfer as successful, exactly the invariant break described in the seed report (code assuming a token behaves like a conforming ERC20 and using that unverified assumption to release value).

### Likelihood Explanation
Requires only that the attacker be the order creator and deploy their own token contract — no relayer collusion, no admin/governance action, and no cross-chain proof forgery. It relies on Tron's `SELFDESTRUCT` semantics clearing contract code from a separate transaction than creation (distinct from EIP-6780-restricted Ethereum mainnet behavior), which is directly relevant since this file lives under `evm/tron/contracts/apps/`, the Tron-specific deployment of the gateway.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` release paths in `withdraw()`, the fee-release block, and `SweepDust` with `SafeERC20.safeTransfer`, consistent with the deposit path — `SafeERC20` already validates both contract-code presence and any returned boolean data, closing the trivial-success gap.

### Proof of Concept
1. Attacker deploys `EvilToken` (standard ERC20 with a `kill()` function calling `selfdestruct`).
2. Attacker calls `placeOrder` with `order.inputs = [EvilToken, amount]`, depositing real `amount` via `safeTransferFrom` (succeeds, `EvilToken` has code).
3. A solver calls `fillOrder`, transferring real output tokens to the attacker's beneficiary address (irreversible).
4. Attacker calls `EvilToken.kill()`, destroying the contract code on Tron before the `RedeemEscrow` request is delivered/executed.
5. When `onAccept` → `withdraw` executes, `token.call(...)` against the codeless `EvilToken` address returns `success = true` with no tokens moved; `EscrowReleased` fires and `_orders[...]` is decremented, but the solver receives nothing for the output tokens they already paid.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-454)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L708-713)
```text
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
```
