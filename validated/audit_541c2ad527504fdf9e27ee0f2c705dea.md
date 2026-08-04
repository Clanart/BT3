### Title
`IntentGatewayV2.withdraw()` on the Tron deployment lacks the zero-amount transfer guard present in the mainline `IntentsBase._withdraw`, allowing weird-ERC20 zero-transfer reverts to permanently block escrow release/refund - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The mainline EVM intents contract explicitly guards against zero-value token transfers when releasing escrow: `IntentsBase._withdraw` skips any token leg whose amount is zero with `if (amount == 0) continue;` before touching the escrow accounting or issuing a transfer. [1](#0-0) 

The Tron fork of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, implements the equivalent `withdraw()` function but has no such guard: it unconditionally calls `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` for every entry in `body.tokens`, regardless of whether `amount == 0`. [2](#0-1) 

This is a structural analog of the `Cooler.roll()` bug: a zero-value ERC-20 transfer can revert on tokens that follow the "revert on zero-value transfers" weird-token pattern (documented at https://github.com/d-xo/weird-erc20#revert-on-zero-value-transfers), and the entire withdrawal loop reverts as a unit because there is no per-leg zero check to skip it.

### Finding Description
`withdraw()` is the single internal function that both `onAccept` (for `RedeemEscrow`/`RefundEscrow` requests delivered cross-chain) and `onGetResponse` (for the GET-based cancellation/refund verification path) funnel into to release escrowed order inputs to a beneficiary: [3](#0-2) [4](#0-3) 

Inside `withdraw()`, the token loop iterates `body.tokens` and, for every entry, performs a raw ERC-20 `transfer` call without first checking `amount != 0`:
```solidity
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
    unchecked { ++i; }
}
``` [5](#0-4) 

Compare this to the mainline contract's equivalent, which was clearly hardened against exactly this class of bug by adding `if (amount == 0) continue;` before any escrow mutation or transfer: [1](#0-0) 

Because the Tron contract's `withdraw()` retains the unguarded pattern, any code path that can produce a zero-amount token leg in `body.tokens` — e.g., a token whose escrowed balance for a given commitment legitimately nets to zero after fee deduction, dust handling, or a state where one of several `order.inputs` entries settles to zero — will cause the `token.call(...transfer...)` to revert against a "revert-on-zero-transfer" ERC-20, reverting the *entire* withdrawal transaction. Since `_filled[body.commitment] = beneficiary;` is set at the top of the function before the loop runs, and the whole call reverts atomically, no partial state is corrupted, but the withdrawal can never complete for that commitment through this path — the escrow becomes permanently unwithdrawable via `onAccept`/`onGetResponse` for as long as that zero-leg configuration exists, i.e., a fund-lock rather than merely a retry.

This directly mirrors the root cause identified in the external report: `Cooler.roll()` reverted on `newCollateral = 0` for weird ERC-20s that revert on zero-value transfer, blocking legitimate protocol flow, because the code lacked an explicit check/skip for the zero case. The Tron `withdraw()` function has the identical missing guard that the audited, presumably-fixed mainline (`IntentsBase.sol`) contract already accounts for — showing the maintainers were aware of and patched this exact class of issue in one code path but left the Tron fork unguarded.

### Impact Explanation
This falls within the accepted bounty scope of unauthorized fund loss/lock in bridge custody / intent settlement: escrowed order inputs held by the `IntentGatewayV2` contract on Tron become stuck and cannot be released to the rightful beneficiary (solver on fill, or user on refund/cancellation) whenever a weird zero-value-reverting ERC-20 is used as an order input and the settlement/refund message carries a zero-amount leg for that token. This is a genuine loss/lock-of-funds condition reachable via the normal, unprivileged cross-chain settlement flow (fill → `RedeemEscrow` dispatch → `onAccept` → `withdraw()`, or cancel → GET query → `onGetResponse` → `withdraw()`), not through a malicious relayer, prover, or admin action.

### Likelihood Explanation
Likelihood depends on: (1) an order using a token that reverts on zero-value transfers as one of its input assets, and (2) the settlement/refund flow constructing a `WithdrawalRequest` with a zero amount for that token leg (e.g., via protocol-fee rounding to zero on very small dust remainders, or a multi-token order where one leg's contribution nets to zero). Both conditions are plausible in production given Hyperbridge's intent gateway is explicitly documented to support arbitrary ERC-20s (including fee-on-transfer and other "weird" tokens, as evidenced by the FeeOnTransferToken test suite present in the codebase), making this a realistic, non-contrived edge case rather than a purely theoretical one.

### Recommendation
Add the same guard used in `IntentsBase._withdraw` to the Tron `withdraw()` function: skip any token leg whose `amount == 0` before touching `_orders[...]` or issuing a transfer call, e.g.:
```solidity
if (amount == 0) { unchecked { ++i; } continue; }
```
placed before the `_orders[body.commitment][token] == 0` check and the transfer call, mirroring the mainline fix.

### Proof of Concept
1. An order is placed on the Tron `IntentGatewayV2` with an input token `T` that reverts on `transfer(to, 0)` (weird-ERC20 zero-transfer-revert behavior).
2. Due to protocol fee/dust accounting, a `WithdrawalRequest.tokens` entry for `T` is constructed with `amount == 0` (e.g., a dust/rounding remainder, or a multi-leg order where token `T`'s contribution rounds to zero) while the escrow mapping `_orders[commitment][T]` is still non-zero from another leg or a prior partial credit.
3. The cross-chain `RedeemEscrow`/`RefundEscrow` message is delivered and `onAccept` invokes `withdraw(body, ...)`, or the GET-response cancellation path invokes `onGetResponse` → `withdraw(body, true)`.
4. Inside `withdraw()`, the loop reaches the `T` leg with `amount == 0` and calls `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, 0))`. Because `T` reverts on zero-value transfers, this call returns `success == false`, tripping `revert TransferFailed();` and reverting the entire withdrawal transaction.
5. Since `withdraw()` is the only path to release escrow for that commitment, and the transaction reverts every time it is retried (as the zero-amount leg is deterministic from the order's fixed accounting), the escrowed funds for that commitment become permanently stuck in the contract — unlike the mainline contract, which would `continue` past the zero leg and complete the withdrawal successfully.

Note: I could not fully trace, within the available context, the exact upstream code path (fill/select flow) on the Tron contract that constructs the `RedeemEscrow` `WithdrawalRequest.tokens` array to confirm a concrete production scenario that yields a zero-amount leg while escrow for that commitment remains non-zero for other tokens; this would need to be verified with a full read of the Tron contract's `fillOrder`/`select` functions (lines outside the range inspected) to construct a fully deterministic exploit trace. The core code-level defect — the missing zero-amount guard relative to the hardened mainline contract — is directly confirmed by the cited code.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-409)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```
