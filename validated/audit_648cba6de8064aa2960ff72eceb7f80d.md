### Title
`_paused` circuit breaker exists but is never enforced on IntentGateway entrypoints — [File: evm/src/apps/intentsv2/IntentsBase.sol]

### Summary
`IntentsBase.sol` declares a `_paused` state flag intended to act as an emergency circuit-breaker for the Intent Gateway, but no function in the inheriting contracts (`ExtrinsicIntents.sol`, `IntrinsicIntents.sol`) ever reads it. There is no `whenNotPaused` modifier and no `if (_paused) revert` check anywhere in the codebase that gates `placeOrder`, `fillOrder`, `cancelOrder`, or `onAccept`. This mirrors the Astaria Private Vault finding exactly: a pause/shutdown flag exists in storage, but the deposit-equivalent entrypoint (`placeOrder`, which escrows user funds) and the settlement entrypoints (`fillOrder`, `onAccept`/`_withdraw`) never check it.

### Finding Description
`_paused` is declared at [1](#0-0)  with the comment "Appended last to preserve existing storage slots," indicating it was added to support pausability in an upgrade. However:
- A repo-wide grep for `whenNotPaused`, `_paused`, `pause()`, `Pausable` inside `evm/src/apps/intentsv2/ExtrinsicIntents.sol` and `evm/src/apps/intentsv2/IntrinsicIntents.sol` returns zero matches — meaning the two contracts that implement the actual entrypoints (`placeOrder`, `fillOrder`, `cancelOrder`, `select`, `onAccept`) never consult `_paused`.
- Within `IntentsBase.sol` itself, `_paused` appears only in its declaration; the shared internal logic it exposes — `_withdraw` (releases/refunds escrow), `_execute` (calldata execution), `_sweepDust`, `_updateParams`, `_addDeployment` — likewise contains no pause check [2](#0-1) .
- Contrast this with the other EVM apps in the same repo (`HyperFungibleToken`, `HyperFungibleTokenUpgradeable`, `WrappedHyperFungibleToken`, `HyperbridgeLzEndpoint`), which all correctly gate `send()` and `onAccept()`/`onPostRequestTimeout()` with `whenNotPaused` [3](#0-2) . The Intent Gateway is the outlier: it has the storage slot for a pause flag but wired it to nothing.

This is the direct analog of the Astaria bug: `VIData.isShutdown` (or here, `_paused`) exists as a protective flag, but the fund-moving entrypoint (`deposit` in Astaria, `placeOrder`/`fillOrder`/escrow release in Hyperbridge) omits the guard that would have blocked activity while the protocol is supposed to be halted.

### Impact Explanation
Escrowed user funds move through `placeOrder` (locks user input tokens), `fillOrder` (releases escrow to solver, executes calldata via `_execute`), and cross-chain settlement via `onAccept`/`_withdraw` (releases escrow to solver/refunds user) — all without any dependency on `_paused`. If governance (via `UpdateParams`/upgrade path) ever sets `_paused = true` intending to halt the gateway during an incident (e.g., a discovered exploit, an oracle malfunction, a pending upgrade), attackers and solvers can continue to `placeOrder`, `fillOrder`, and drain/settle escrow exactly as before. This defeats the entire purpose of the flag and can result in continued fund movement, incorrect settlement, or fund loss during exactly the window the protocol operators believed operations were frozen — a direct "unauthorized transaction/execution" and "logic attack" per the bounty's impact gate, since no privileged/malicious actor is required to exploit it: it's simply that the safety check silently does nothing for any caller, honest or otherwise.

### Likelihood Explanation
High confidence that the flag is dead code based on direct grep evidence across all Intent Gateway v2 source files: `_paused` is set nowhere reachable, checked nowhere, and no `pause()`/`unpause()` function exists in the searched files. The risk materializes as soon as anyone (governance or an emergency responder) attempts to rely on this flag to halt the gateway — it silently fails to do so, and every unprivileged solver/user can keep calling `placeOrder`/`fillOrder` without any additional preconditions.

### Recommendation
Add a `whenNotPaused` modifier (checking `_paused`) to `placeOrder`, `fillOrder`, `cancelOrder`, `select`, and `onAccept` in `ExtrinsicIntents.sol` and `IntrinsicIntents.sol`, mirroring the pattern already used correctly in `HyperFungibleToken`/`WrappedHyperFungibleToken`. Add corresponding `pause()`/`unpause()` governance-gated setters if none exist, and add regression tests asserting each entrypoint reverts when `_paused == true`.

### Proof of Concept
1. Confirm `_paused` declaration in `IntentsBase.sol`: [1](#0-0) .
2. Grep `evm/src/apps/intentsv2/ExtrinsicIntents.sol` and `evm/src/apps/intentsv2/IntrinsicIntents.sol` for `_paused`/`whenNotPaused`/`pause` — zero matches, proving `placeOrder`/`fillOrder`/`cancelOrder`/`onAccept` never check it.
3. Compare against `HyperFungibleTokenUpgradeable.sol`'s `send()`/`onAccept()`, which do gate on `whenNotPaused` [4](#0-3) , showing the Intent Gateway is inconsistent with the rest of the codebase's pause discipline.
4. Even with `_paused` manually flipped to `true` via storage (simulating a governance pause intent), any call to `placeOrder`/`fillOrder` in `ExtrinsicIntents.sol`/`IntrinsicIntents.sol` would proceed unaffected because no code path reads `_paused`.

Note: I was unable to fully view `ExtrinsicIntents.sol` and `IntrinsicIntents.sol` in this session (only confirmed via grep that no pause-related tokens exist in them), so the exact signatures of `placeOrder`/`fillOrder`/`cancelOrder` and whether any alternate emergency-stop mechanism exists elsewhere in the proxy/governance layer could not be verified beyond what grep/search surfaced. A Devin session with full file access should confirm this before treating it as final.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L160-161)
```text
    /// @dev Appended last to preserve existing storage slots.
    bool public _paused;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L293-320)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
        _burn(msg.sender, params.amount);
        DispatchPost memory request = _buildDispatchPost(params);

        bytes32 commitment;
        if (msg.value > 0) {
            commitment = IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
            commitment = dispatchWithFeeToken(request);
        }

        emit Sent({
            from: msg.sender,
            to: params.to,
            dest: string(params.dest),
            amount: params.amount,
            commitment: commitment
        });
    }

    /**
     * @notice Handles incoming cross-chain token transfer messages
     * @dev Called by the ISMP host when a POST request is received. Verifies the source
     * address matches the configured contract for that chain, then mints tokens to the
     * recipient. If calldata is present, executes it via the CallDispatcher.
     * @param incoming The incoming POST request containing the token transfer message
     */
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
```
