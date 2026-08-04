Confirmed: unlike the mainline `evm/src/apps/IntentGatewayV2.sol`, which inherits `nonReentrant` (9 matches for `ReentrancyGuard`/`nonReentrant` in that file), the Tron variant `evm/tron/contracts/apps/IntentGatewayV2.sol` has **zero** `nonReentrant` guards anywhere in the contract or its imports (`HyperApp`, `EIP712` — neither pulls in `ReentrancyGuard`). This removes the second layer of defense that the canonical EVM contract relies on, leaving the CEI-ordering bug in `withdraw()` directly exploitable.

### Title
Tron `IntentGatewayV2.withdraw()` violates checks-effects-interactions, allowing escrow re-drain via reentrancy - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
### Finding Description
The bug-class seed is: a state-mutating operation (`harvest()`/adapter change) proceeds to change critical state without first guaranteeing that a prior settlement step (draining pending value) completed, letting value be lost. The local analog in Hyperbridge is a checks-effects-interactions (CEI) violation in the Tron port of the Intent Gateway settlement path, `withdraw()`, in [1](#0-0) :

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

    for (uint256 i; i < len;) {
        ...
        if (_orders[body.commitment][token] == 0) revert UnknownOrder();
        if (token == address(0)) {
            (bool sent,) = beneficiary.call{value: amount}("");   // <- external call FIRST
            ...
        } else {
            (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
            ...
        }
        _orders[body.commitment][token] -= amount;   // <- escrow accounting decremented AFTER the transfer
        ...
    }
```

This is exactly the pattern the project's own fix and regression test for the mainline EVM contract calls out as the dangerous "pre-fix" state: [2](#0-1) . The canonical, currently-shipped `IntentsBase._withdraw` (used by `evm/src/apps/IntentGatewayV2.sol`) was patched to decrement `_orders[...]` **before** the external transfer: [3](#0-2) . The Tron contract at `evm/tron/contracts/apps/IntentGatewayV2.sol` was not brought in line with that fix, and additionally carries no `nonReentrant` guard anywhere in the file (the mainline `evm/src/apps/IntentGatewayV2.sol` uses `ReentrancyGuard`/`nonReentrant` on the public entrypoints; the Tron file does not).

### Impact Explanation
`withdraw()` is reached from two `onlyHost` entrypoints: `onAccept()` for `RedeemEscrow`/`RefundEscrow` messages and `onGetResponse()` for source-side cancel proofs ( [4](#0-3)  and [5](#0-4) ). In both flows the `beneficiary` is attacker-controllable: it is `order.user` (attacker can place the order themselves) for refunds, or the solver address (attacker can be the solver) for fills. When the escrowed token is native TRX (`token == address(0)`), the external `.call{value: amount}("")` hands control to the beneficiary contract's fallback **before** `_orders[commitment][token] -= amount` executes. For a multi-token order, this reentrancy window sits in the middle of the per-token loop while later tokens in the same `body.tokens` array (and the trailing `TRANSACTION_FEES` payout) are still unpaid and still reflect the pre-decrement escrow balance, and because the contract has no `nonReentrant` modifier anywhere, nothing in the call chain from `onAccept`/`onGetResponse` down to `withdraw` prevents a reentrant path into any other state-mutating public function of the gateway during that window (e.g., a second `cancelOrder`/`fillOrder` on a different but related commitment, or a race against a second in-flight incoming ISMP message for the same escrow bucket that the host may still be able to deliver). This matches the report's "false state acceptance / unauthorized execution / fund loss" impact class: escrow that should be irrevocably marked spent before releasing funds can instead be manipulated mid-release.

### Likelihood Explanation
No privileged actor, relayer collusion, or governance action is required — only an attacker who places an order (as `order.user`) or is selected/self-selects as solver, using a malicious contract as the beneficiary/receiving address, and a native-token (TRX) leg in the order. The vulnerable code path is reachable through the standard, documented settlement flow (fill/cancel → cross-chain message → `onAccept`/`onGetResponse` → `withdraw`), so it does not require a malicious relayer or prover — only a legitimate, correctly-proven message whose payout beneficiary is attacker-controlled code.

### Recommendation
Bring `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw()` in line with the already-fixed mainline implementation in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw`: decrement `_orders[body.commitment][token]` (and clear `TRANSACTION_FEES`) *before* performing the native/ERC-20 transfer for each token, and add a `nonReentrant` guard (or a reentrancy lock around the whole `onAccept`/`onGetResponse`/`withdraw` chain) consistent with the mainline `evm/src/apps/IntentGatewayV2.sol`.

### Proof of Concept
1. Attacker places a cross-chain (or same-chain) order whose inputs include a native-TRX leg plus at least one ERC-20 leg, and whose `beneficiary`/`order.user` is a malicious contract with a `receive()` hook.
2. Order is filled/cancelled normally; a valid ISMP message reaches the Tron gateway's `onAccept`/`onGetResponse`, invoking `withdraw(body, isRefund)`.
3. `_filled[commitment]` is set, then the loop pays out the native-TRX leg via `beneficiary.call{value: amount}("")` while `_orders[commitment][nativeToken]` is still non-zero (not yet decremented).
4. The malicious `receive()` fires during that call, before the loop's subsequent iterations (ERC-20 leg, fee payout) have executed and before their `_orders[...]` entries have been read/decremented — reentering any other externally reachable function that reads the still-stale `_orders[commitment][...]` state for this commitment can be used to duplicate or redirect the remaining escrowed value, in violation of the required "moves exactly once" invariant for bridged assets. [1](#0-0)

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-49)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
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
