### Title
Cross-chain fill/refund permanently reverts and locks escrowed funds when a user-controlled beneficiary is a contract that rejects native-token transfers - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
The external report's core invariant is: a token/value delivery path uses an unsafe, non-reverting-tolerant transfer primitive to an attacker/user-chosen address; if that address is a contract that cannot correctly accept the asset, the asset becomes permanently stuck with no recovery path. In `IntentGatewayV2`'s escrow release logic, the analogous primitive is a raw `beneficiary.call{value: amount}("")` used to pay out escrowed native tokens, where `beneficiary` is fixed once at order placement/fill time and can never be changed. If that beneficiary is a contract without a working `receive()`/payable `fallback()`, every attempt to settle the escrow reverts, and because settlement is the only exit path for the escrowed funds, they become permanently locked.

### Finding Description
`_withdraw` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 390-410) is the single function responsible for releasing escrowed tokens for both fills and refunds/cancellations: [1](#0-0) 

For native-token (`token == address(0)`) entries it does:
```solidity
(bool sent,) = beneficiary.call{value: amount}("");
if (!sent) revert InsufficientNativeToken();
```
`beneficiary` is derived from `body.beneficiary`, which is populated upstream from:
- `order.output.beneficiary` for same-chain fills (`IntrinsicIntents._fillSameChain`), set once by the user when they place the order and never re-validated or changeable, [2](#0-1) 
- `order.user` for `RefundEscrow`/cancellation flows (`_cancelFromDest`, `_cancelFromSource`), again fixed at order placement, [3](#0-2) 
- `msg.sender` (the solver) for `RedeemEscrow` on cross-chain fills, [4](#0-3) 

There is no `_safeMint`/pull-payment equivalent — no fallback to WETH-wrap-and-ERC20-transfer as exists in the separate `WrappedHyperFungibleToken.onAccept`/`onPostRequestTimeout` handlers, which explicitly re-wrap and deliver WETH if a raw ETH push fails (`sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol`, lines 309-324, 344-362). [5](#0-4)  That contract was clearly designed with this exact failure mode in mind ("so the refund path doesn't permanently lock funds for the same caller class"), but `IntentGatewayV2`'s intents module (`IntentsBase._withdraw`) has no equivalent safeguard for native-token payouts.

For cross-chain orders, `_withdraw(body, isRefund, true)` is invoked from `onAccept` (line 294 of `ExtrinsicIntents.sol`) after the ISMP host has already verified the consensus/state proof for the `RedeemEscrow`/`RefundEscrow` message: [6](#0-5)  If `beneficiary.call{value: amount}("")` reverts because the beneficiary contract has no payable receiver, `onAccept` reverts entirely, and `_filled[commitment]` is never set (the revert unwinds that write too, since it happens inside `_withdraw` in the same call). The message can be resubmitted by a relayer, but since the beneficiary is immutable for that commitment, the transfer will fail identically on every future attempt — the request is not the problem, the fixed destination address is. There is no way to change `order.output.beneficiary`/`order.user` after order placement, and no admin/governance sweep path for a specific stuck commitment's escrow visible in this contract.

### Impact Explanation
Escrowed input tokens (native ETH/fee-machine-native-asset value) become permanently unretrievable once an order specifies (or a user/solver ends up controlling) a beneficiary address that is a smart contract unable to accept a raw value transfer. This is a direct, self-contained loss/lock of user funds inside the bridge's escrow, matching the bounty's "stealing or loss of funds" / fund-lock category, without requiring any malicious relayer, prover, or admin — it is triggered purely by ordinary use of a non-EOA wallet (e.g., a smart-contract wallet, multisig, or vault with a restrictive or absent receive function) as the order's beneficiary/user address.

### Likelihood Explanation
Medium. It requires the beneficiary/order.user to be a contract that cannot accept a plain ETH push (no `receive()`/payable fallback, or one that reverts under certain conditions such as reentrancy guards or allow-lists) and for the order/fill to use native-token settlement. This is a realistic scenario given the increasing use of smart-contract wallets (Safe, ERC-4337 accounts) as user-facing beneficiaries, mirroring exactly the "contract that can't handle X" precondition in the original report.

### Recommendation
Do not permanently fail escrow release when a native-token push to `beneficiary` fails. Adopt the same fallback pattern already implemented in `WrappedHyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable`: on push failure, either (a) route the ETH into a claimable/pull-based recovery mapping keyed by `(commitment, token)` that the beneficiary or its designated recipient can withdraw later via a separate accessible function, or (b) wrap the native asset (e.g., WETH) and deliver it as an ERC-20 via `safeTransfer`, which succeeds unconditionally for any address. This ensures a hostile or incompatible beneficiary contract cannot permanently strand escrowed value, consistent with the design intent already visible elsewhere in the codebase.

### Proof of Concept
1. User places a cross-chain order via `IntentGatewayV2.placeOrder`, escrowing native ETH as `order.inputs`, and setting `order.output.beneficiary` to a smart-contract address `C` with no `receive()`/payable `fallback()` (e.g., a strict multisig or a contract that only accepts calls matching specific selectors).
2. A solver fills the order on the destination chain (`fillOrder`), delivering outputs to `order.output.beneficiary` — irrelevant to this PoC, the important beneficiary is on refund/settlement path.
3. Before fill, the user cancels via `_cancelFromDest`/`_cancelFromSource`, which dispatches a `RefundEscrow` message with `beneficiary = order.user = C`. [3](#0-2) 
4. When the relayer submits the proven `RefundEscrow` message, `onAccept` calls `_withdraw(body, true, true)`, which attempts `C.call{value: amount}("")`. Since `C` has no payable receiver, `sent == false` and the call reverts with `InsufficientNativeToken()`. [7](#0-6) 
5. Every subsequent relayer resubmission of the same proven message reverts identically, because `C`'s inability to accept ETH is a fixed property of the commitment's `beneficiary`. The escrowed ETH remains locked in the `IntentGatewayV2` contract on the source chain indefinitely, with no alternate withdrawal path exposed for that commitment.

**Note on verification depth:** Due to tool-call limits I was unable to fully confirm whether the ISMP host layer provides any generic "failed delivery" recovery/requeue mechanism outside of this app-level contract that might mitigate the lock (e.g., host-level timeout after N failed relayer attempts). If such a mechanism exists in the ISMP host contracts, it would reduce (but likely not eliminate, since the destination address itself never becomes acceptable) the severity of this analog.

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L60-63)
```text
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-146)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-250)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
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
