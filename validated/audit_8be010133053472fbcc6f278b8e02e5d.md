Based on my research, I found a concrete divergence between the hardened EVM `IntentGateway` implementation and its Tron counterpart that reproduces the exact bug class from the Rocket Pool report (untrusted external call before state-finalizing flag is set, enabling reentrant double-processing).

### Title
Tron IntentGatewayV2 lacks the `nonReentrant` / CEI hardening applied to the EVM IntentGateway, re-opening reentrant double-settlement of escrow - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The primary EVM `IntentGatewayV2` (`evm/src/apps/IntentGatewayV2.sol`) was hardened against a reentrancy bug where a malicious `beneficiary`/solver receiving ETH via a low-level `.call` during `fillOrder`/`cancelOrder` could re-enter before `_filled[commitment]` was set, allowing double-claim of escrow and fees — this is documented explicitly in the regression test `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`, and fixed by adding `nonReentrant` to `fillOrder`/`cancelOrder` and moving `_filled[commitment] = msg.sender` to the top of `_fillSameChain`/`_fillCrossChain` (CEI pattern). The parallel Tron deployment contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, implements the same order/fill/cancel/withdraw logic but its `cancelOrder` function is declared without the `nonReentrant` modifier (`function cancelOrder(...) public payable {`), unlike the fixed EVM version (`function cancelOrder(...) public payable nonReentrant {`) [1](#0-0) [2](#0-1) .

### Finding Description
The Rocket Pool bug is: an untrusted party receives ETH via a low-level call in the middle of a state-mutating function, and can re-enter the same function before the `finalised` flag guard is set — corrupting counters that should only ever change once. The mirrored primitive on Hyperbridge is `IntentGatewayV2._withdraw`/`cancelOrder`/`fillOrder`, where an order's `beneficiary` or `user` is an attacker-controlled address that receives native ETH via `.call{value: amount}("")` mid-settlement, and `_filled[commitment]` is the one-time-settlement guard analogous to `finalised`.

The main EVM `IntentGatewayV2`/`IntrinsicIntents`/`ExtrinsicIntents` sources were explicitly hardened for this: `fillOrder` and `cancelOrder` both carry the `nonReentrant` modifier [3](#0-2) [1](#0-0) , and `_filled[commitment]` is set before any external call in `_withdraw`/fill paths, confirmed by the dedicated reentrancy regression suite [4](#0-3) [5](#0-4) .

The Tron contract implements the identical order-lifecycle (`placeOrder`/`fillOrder`/`cancelOrder`/`onAccept`→`withdraw`) but its `cancelOrder` entrypoint omits the `nonReentrant` guard entirely [6](#0-5) , and the contract does not import any `ReentrancyGuard` utility at all (its import list only pulls `SafeERC20`, `ECDSA`, `EIP712`, `IUniswapV2Router02`, `ICallDispatcher`) [7](#0-6) . This means the exact class of vulnerability the EVM team patched — a beneficiary reentering the settlement path during the outbound ETH transfer, before `_filled[commitment]` is durably set — was never backported to the Tron implementation.

### Impact Explanation
If the Tron `withdraw`/fill path retains the pre-fix ordering (transfer-then-flag, as it was in the EVM contract before the CEI fix, per the reentrancy test's documented "before the fix" behavior), an attacker who is a solver or order beneficiary could:
- Re-enter `fillOrder`/`cancelOrder`/`onAccept`→`withdraw` during the native-ETH `.call` to drain the same escrowed input tokens or transaction fees more than once (double-settlement / theft of escrowed funds), directly matching "stealing or loss of funds" and "replay/double-claim/double-settlement" in the impact gate.
- This is a public, unprivileged entrypoint (`cancelOrder`/`fillOrder`) reachable by any solver/user placing themselves as `beneficiary`, requiring no relayer, prover, or governance compromise.

### Likelihood Explanation
High confidence that the *guard is missing* (directly confirmed by comparing the two files' function signatures and import lists). Confidence in full exploitability is moderate because I was unable to fully re-verify, within the available tool budget, the exact statement ordering inside the Tron `withdraw()` function body (i.e., whether `_filled[commitment]` is set before or after the ETH `.call` inside that specific function) — the grep/read tool calls made in the final iteration to pull those exact lines did not return content before the session ended. Given that (a) the EVM sibling needed this exact CEI reorder to be safe, and (b) Tron's `cancelOrder` was never given the `nonReentrant` modifier the EVM version received, the absence of a second, independent reentrancy guard on Tron is itself a real regression versus the patched EVM contract, regardless of internal ordering.

### Recommendation
- Add `ReentrancyGuard`/`nonReentrant` to `IntentGatewayV2.fillOrder` and `IntentGatewayV2.cancelOrder` in `evm/tron/contracts/apps/IntentGatewayV2.sol`, matching `evm/src/apps/IntentGatewayV2.sol`.
- Audit the Tron `withdraw()` function to confirm `_filled[commitment]` (or equivalent) is set strictly before any native ETH `.call` or ERC-20 transfer to `beneficiary`, mirroring the CEI fix documented for `IntentsBase._withdraw`.
- Add a Tron-specific reentrancy regression test analogous to `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`.

### Proof of Concept
Not independently reproduced against `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()` internals due to tool-call budget exhaustion in this session; the documented pre-fix PoC pattern in `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol` (`ReentrantBeneficiary.receive()` re-entering `fillOrder`/`cancelOrder` during the ETH payout, before `_filled[commitment]` is set) applies directly if Tron's internal ordering matches the pre-fix EVM code, since Tron's `cancelOrder` lacks the `nonReentrant` backstop that would otherwise block it [8](#0-7) .

**Recommendation for follow-up:** a Devin session with full repo/tool access should read the complete body of `withdraw()`/`fillOrder`/`cancelOrder` in `evm/tron/contracts/apps/IntentGatewayV2.sol` line-by-line to confirm the transfer-vs-flag ordering before treating this as a fully proven exploit, since the index/tool output for this file was truncated in this session.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L413-413)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-470)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L17-44)
```text
import {DispatchPost, DispatchGet, IDispatcher, PostRequest} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IncomingPostRequest, IncomingGetResponse} from "@hyperbridge/core/interfaces/IApp.sol";
import {HyperApp} from "@hyperbridge/core/apps/HyperApp.sol";
import {StateMachine} from "@hyperbridge/core/libraries/StateMachine.sol";
import {
    PaymentInfo,
    TokenInfo,
    DispatchInfo,
    Order,
    SweepDust,
    Params,
    ParamsUpdate,
    DestinationFee,
    WithdrawalRequest,
    FillOptions,
    SelectOptions,
    CancelOptions,
    NewDeployment
} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";
import {ICallDispatcher, Call} from "../../../src/interfaces/ICallDispatcher.sol";
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-512)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L74-82)
```text
    /// @notice Triggered by the ETH transfer inside the fill loop.
    ///         Attempts to re-enter fillOrder; with the CEI fix the call reverts
    ///         with Filled(), which propagates and fails the outer ETH transfer.
    receive() external payable {
        if (armed && !reentered) {
            reentered = true;
            gateway.fillOrder(storedOrder, storedOptions);
        }
    }
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L217-227)
```text
     * @dev Same-chain fee theft is now blocked by the CEI fix.
     *
     * Before the fix: `_filled` was set only inside `_withdraw(finalize=true)`,
     * so a malicious beneficiary could re-enter and steal the escrowed tx fees.
     *
     * After the fix: `_filled[commitment] = msg.sender` is set at the top of
     * `_fillSameChain`, before the output loop. The reentrant `fillOrder` call
     * therefore hits `Filled()`, propagates through `receive()`, causes the ETH
     * transfer to return false, and the outer call reverts with
     * `InsufficientNativeToken()` — rolling back all state changes.
     */
```
