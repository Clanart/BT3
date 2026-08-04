### Title
Permanent lock of escrowed order funds when `withdraw()` sends native tokens to a beneficiary without a receive/fallback function - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` unconditionally pushes native-token escrow to `beneficiary` via a raw `call{value: amount}("")` and reverts the entire transaction (including all escrow bookkeeping) if that call fails. Because there is no alternate delivery path (unlike ERC20 which can be re-attempted or bridged through a wrapper), any order whose escrow beneficiary is a contract without `receive()`/`fallback()` permanently bricks the settlement: the escrow can never be released, the solver's `RedeemEscrow` payment can never be redeemed, and the order-creator's cancellation refund can never be executed. This mirrors the referenced Taiko `ERC20Vault` finding — unconditional native transfer with a hard revert on failure and no failure-handling escape hatch.

### Finding Description
In `withdraw()`, the escrow release/refund path does: [1](#0-0) 

For any native-token (`token == address(0)`) line item, it performs:
```solidity
(bool sent,) = beneficiary.call{value: amount}("");
if (!sent) revert InsufficientNativeToken();
```
If `beneficiary` is a contract that implements neither `receive()` nor a payable `fallback()`, `sent` is `false` and the whole `withdraw()` call reverts. This function is invoked from two production entry points:

1. `onAccept` handling `RequestKind.RedeemEscrow` (escrow release to the solver after a successful cross-chain fill), reached via the host's authenticated `onAccept` callback.
2. `onGetResponse`, which finalizes an order cancellation/refund to the original order creator once the GET query proves the order was never filled on the destination chain: [2](#0-1) 

The beneficiary in both flows is attacker/user-controlled: the solver's own address is embedded as `beneficiary` in the `RedeemEscrow` withdrawal request when filling cross-chain (`beneficiary: bytes32(uint256(uint160(msg.sender)))`), and the order creator's address (`order.user`, set to `msg.sender` at order-creation time) becomes the refund beneficiary on cancellation: [3](#0-2) 

Because `_filled[body.commitment] = beneficiary` and the `_orders[...] -= amount` decrement occur in the same function that performs the raw `call`, a failed native transfer reverts the whole state transition — there is no `retryMessage`/`failMessage` equivalent, no fallback to an ERC20-wrapped delivery, and no way to redirect the payout to a different address. The escrowed native asset is permanently stuck in the gateway contract, unreachable by any code path.

### Impact Explanation
This directly causes loss/lock of bridged/escrowed funds — the core impact class targeted by the bounty (bridge custody / intent settlement fund loss). Once triggered, escrowed native-asset orders can never be settled:
- A solver who fills an order and expects `RedeemEscrow` payment from a contract wallet without `receive()`/`fallback()` permanently forfeits payment even though they already delivered outputs to the beneficiary on the destination chain.
- An order creator who cancels an unfilled order from a contract without `receive()`/`fallback()` can never recover their locked native-token input — the refund path (`onGetResponse` → `withdraw(body, true)`) will revert forever.

Unlike the original Taiko fix (which added `retryMessage`/`failMessage`/`recallMessage` escape hatches), this codebase has no analogous recovery mechanism for a reverting native transfer in `withdraw()`.

### Likelihood Explanation
No malicious peer, relayer, prover, or admin action is required — an ordinary user (order creator) or solver simply needs to use a smart-contract wallet without a payable fallback as their address when interacting with `IntentGatewayV2`. This is a realistic scenario for smart-contract-based solvers (arbitrage bots, vault contracts) and for users routing intents through non-payable proxy/multisig wallets. The failure is deterministic and triggers on the very first attempted settlement/refund for native-asset orders.

### Recommendation
- Do not let a failed native transfer revert the entire escrow-accounting state change; decrement `_orders`/mark `_filled` first (checks-effects-interactions) and, on transfer failure, credit the amount to an internal per-beneficiary "pending withdrawal" balance that can be pulled later (pull-payment pattern) instead of pushed.
- Alternatively, on failed native `call`, fall back to wrapping the native asset (e.g., WETH) and delivering it as an ERC20 transfer, mirroring the pattern already used in `WrappedHyperFungibleToken.onAccept`/`onPostRequestTimeout`: [4](#0-3) 
- Add an owner/governance-gated recovery function to redirect stuck escrow to an alternate address after a timeout, similar to Taiko's `retryMessage`/`failMessage`/`recallMessage` mitigation.

### Proof of Concept
1. Deploy a contract `NoFallback` with no `receive()`/`fallback()`.
2. As a solver, call the cross-chain fill function using `NoFallback` as `msg.sender` (or route the fill through a `NoFallback`-controlled EOA proxy contract) so that `order.user`/solver beneficiary in the escrow redemption resolves to `NoFallback`.
3. Trigger the destination chain's `RedeemEscrow` dispatch back to the source chain; the host calls `onAccept` → `withdraw(body, false)`.
4. Observe that `(bool sent,) = beneficiary.call{value: amount}("")` returns `false` because `NoFallback` has no payable fallback, causing `revert InsufficientNativeToken()` and reverting the whole `onAccept` call — escrow remains locked in the gateway with no way to release it to the solver.
5. Repeat with `order.user = address(NoFallback)` and trigger `onGetResponse` after order-deadline cancellation to show the refund path is equally and permanently unusable.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L723-735)
```text
    /**
     * @notice Handles the response for a previously dispatched storage query (GET request).
     * @dev This function is called by the host to process the response of a GET request.
     * @param incoming The response data structure for the GET request.
     * Only the host can call this function.
     */
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
}
```

**File:** evm/src/apps/IntentGatewayV2.sol (L191-194)
```text
        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleTokenUpgradeable.sol (L338-353)
```text
        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```
