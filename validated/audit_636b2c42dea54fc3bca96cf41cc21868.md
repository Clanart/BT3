## Analysis

The exact bug class from the external report — native-ETH `.call{value:}()` transfers that permanently revert (and thus lock funds) when the recipient is a contract without a `receive()`/payable `fallback()` — reappears in Hyperbridge's intent-settlement code, and notably the codebase has already patched this precise pattern elsewhere (`WrappedHyperFungibleToken`), showing the risk is understood but not consistently mitigated.

### Title
Permanent lock of escrowed native tokens in IntentGateway `_withdraw`/`_sweepDust` when beneficiary cannot receive raw ETH - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase._withdraw()`, used by both the fill-settlement (`RedeemEscrow`) and cancellation (`RefundEscrow`) code paths of the Intent Gateway, releases escrowed native tokens with a raw `.call{value: amount}("")` and reverts the entire withdrawal if that call fails. The Tron variant (`IntentGatewayV2.withdraw`) and `_sweepDust()` share the identical pattern. Unlike `WrappedHyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable`, which explicitly fall back to wrapping ETH into WETH and delivering it as an ERC-20 transfer when the native push fails, the IntentGateway code has no such fallback.

### Finding Description
In `_withdraw`, for every token in the withdrawal request the escrow accounting is decremented first, then funds are pushed out: [1](#0-0) 

If `token == address(0)` and the `beneficiary` is a contract without a payable fallback (or one whose fallback exceeds forwarded gas), the `call` fails and the function reverts with `InsufficientNativeToken()`. Because Solidity reverts undo all storage writes in the same transaction, the earlier `_orders[body.commitment][token] = escrowed - amount;` decrement is also rolled back — so the escrow entry is left exactly as before and the same failure will recur on every retry, permanently locking the escrowed native token for that commitment.

This same beneficiary is used both for fill payouts (`RedeemEscrow`, beneficiary = solver `msg.sender` at fill time) and for cancellation refunds (`RefundEscrow`, beneficiary = `order.user`), and identically in the Tron contract's `withdraw()`: [2](#0-1) 

The same unguarded pattern is used for protocol dust sweeps: [3](#0-2) 

The codebase demonstrates it is aware of and has previously fixed this exact class of bug in `WrappedHyperFungibleToken`, where a failed native push falls back to WETH deposit + ERC-20 transfer instead of reverting: [4](#0-3) 

That mitigation is not applied in `IntentsBase._withdraw` / `IntentGatewayV2.withdraw` / `_sweepDust`.

### Impact Explanation
Because the withdrawal decrement and the transfer happen in the same atomic call, a failed native transfer never partially settles — it reverts every attempt, permanently freezing the escrowed native token amount in the gateway contract for that order commitment. If an order mixes a native-token output alongside ERC-20 outputs, the ERC-20 legs are also blocked since they're released in the same loop/transaction as the failing native leg, compounding the fund lock beyond just the native portion. This matches the original report's impact class (loss/lock of funds when the payout target cannot accept raw ETH) but here it applies to actual bridge escrow settlement rather than a single marketplace payout.

### Likelihood Explanation
Any order whose `output.beneficiary` (for fills) or whose creator address (`order.user`, for refunds) resolves to a smart-contract address without a compatible `receive`/`fallback` will trigger this on the very first withdrawal attempt and on every retry thereafter — no privileged actor, malicious relayer, or front-running is required, only a beneficiary/user address that is a non-payable contract, which is an entirely realistic and common condition for smart-contract wallets, vaults, or other protocol contracts interacting with the Intent Gateway.

### Recommendation
Apply the same mitigation already used in `WrappedHyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable`: on native-transfer failure, wrap the stuck native amount (e.g., via WETH deposit) and deliver it as an ERC-20 transfer instead of reverting the whole withdrawal, so escrow settlement/refund always completes and funds are never permanently locked.

### Proof of Concept
1. Deploy a beneficiary contract with no `receive()`/payable `fallback()`.
2. Create an order via `placeOrder` with a native-token (`address(0)`) input and `order.output.beneficiary` set to the non-payable contract (or, for the refund path, place the order from that contract as `order.user`).
3. Either have a solver fill and dispatch `RedeemEscrow`, or let the order expire and dispatch `RefundEscrow` via `_cancelFromSource`/`onGetResponse`.
4. `onAccept`/`onGetResponse` invokes `_withdraw`, which reaches `beneficiary.call{value: amount}("")`; the call fails, the function reverts with `InsufficientNativeToken()`, and the escrow decrement is rolled back.
5. Every subsequent retry of the same withdrawal message reproduces the identical revert — the escrowed native token amount for that commitment is permanently unrecoverable.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L579-597)
```text
    function _sweepDust(SweepDust memory req) internal {
        uint256 outputsLen = req.outputs.length;
        for (uint256 i; i < outputsLen;) {
            TokenInfo memory info = req.outputs[i];
            address token = address(uint160(uint256(info.token)));
            uint256 amount = info.amount;

            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L309-324)
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
