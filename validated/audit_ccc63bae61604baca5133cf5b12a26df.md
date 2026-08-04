### Title
Blacklisted refund recipient in `WrappedHyperFungibleToken.onPostRequestTimeout` permanently blocks batched timeout settlement for all other users - (File: sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol)

### Summary
`WrappedHyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable` hard-code the refund recipient of a timed-out cross-chain transfer to `message.from` (the original sender) with no receiver override, exactly like the PearVault `requestWithdrawal()` bug that forced `msg.sender` as the sole withdrawal recipient. If that address is blacklisted by the wrapped underlying ERC20 (the contract is explicitly built to wrap arbitrary ERC20s, including blacklist-capable tokens like USDC), the `safeTransfer` refund reverts every time. Because `HandlerV2.handlePostRequestTimeouts` processes a whole batch of unrelated timed-out requests inside a single loop with no per-item try/catch, one permanently-reverting refund blocks the entire batch's `dispatchTimeOut` calls, freezing timeout settlement (and the escrowed refunds) for every other user whose timeout happens to be included in the same proof submission.

### Finding Description
`onPostRequestTimeout` decodes the original sender and force-refunds them, with `safeTransfer` reverting on failure and no way to redirect to another address: [1](#0-0) 

The same pattern exists in the upgradeable variant: [2](#0-1) 

Crucially, the ISMP handler dispatches multiple unrelated timed-out requests from one Merkle-proof message in a single, non-isolated loop: [3](#0-2) 

There is no `try/catch` around `host.dispatchTimeOut(...)` — a revert anywhere in the loop (including inside the target app's `onPostRequestTimeout`) reverts the whole transaction. `batchCall` shows the codebase's existing atomic-revert pattern is deliberate elsewhere, but here it applies to *unrelated* requests being timed out together, not to an intentional atomic batch chosen by one caller.

This mirrors the PearVault flaw precisely: a hard-coded, non-redirectable beneficiary that can be rendered permanently untransferable (blacklist), and — just like `totalPendingShares` being blocked for all vault users — this corrupts the shared state needed for *unrelated* users' honest requests to be settled, because those unrelated requests are bundled into the same on-chain proof batch.

### Impact Explanation
Once one dispatched transfer's original sender becomes blacklisted on the underlying token, its timeout can never be delivered: `safeTransfer` will always revert. Since `handlePostRequestTimeouts` processes multiple `PostRequest` timeouts together against one state-machine height proof, any relayer batching this stuck request together with legitimate ones causes the entire transaction to revert, so no other user's timeout in that batch gets processed either. The stuck request's commitment is never cleared (only a successful `dispatchTimeOut` clears it), so it can keep reappearing in future batches and repeatedly poison them. This causes stuck/locked bridged funds for the blacklisted user and, more importantly, blocks refund/timeout settlement (fund recovery) for unrelated users whenever a relayer includes the stuck request in the same MMR-proof timeout message — a shared-state fund-lock DoS matching the accepted bug class in the source report.

### Likelihood Explanation
No privileged actor is required. Any user who sends a `WrappedHyperFungibleToken` cross-chain transfer and is later blacklisted by the wrapped token (or deliberately uses/creates an address they know will become blacklisted, or a contract address that reverts on `receive`/`transfer` in a similar composition) can trigger the unrecoverable revert path. Relayers naturally batch multiple pending timeouts into one submission to save gas/calls, so co-mingling with a poisoned request is a realistic, low-effort occurrence rather than a contrived edge case.

### Recommendation
- Add a way to redirect stuck refunds (e.g., escrow-to-claim pattern: on transfer failure, credit an internal balance the affected address can withdraw to any recipient later) instead of a direct forced `safeTransfer` inside `onPostRequestTimeout`.
- In `HandlerV2.handlePostRequestTimeouts` (and the analogous post-request/get-response handlers), isolate each timeout's `dispatchTimeOut` call (e.g. via a low-level `try/catch` or self-`call`) so one app's failure cannot block delivery of unrelated requests' timeouts in the same batch.
- Allow an admin/permissionless "sweep" path to mark a permanently-failing timeout as settled into a claimable-by-anyone escrow, so it stops corrupting future batches.

### Proof of Concept
1. Alice calls `WrappedHyperFungibleToken.send(...)` to bridge USDC-like tokens cross-chain; `message.from = Alice`.
2. Before the message is delivered, the destination is unreachable/expires, or Alice's address gets blacklisted by the underlying token issuer for an unrelated reason.
3. The request times out. A relayer collects Alice's timeout together with several other users' legitimate timeouts into one `PostRequestTimeoutMessage` batch and calls `HandlerV2.handlePostRequestTimeouts`.
4. The loop reaches Alice's entry, `host.dispatchTimeOut` calls `WrappedHyperFungibleToken.onPostRequestTimeout`, which calls `IERC20(_underlying).safeTransfer(Alice, amount)`; the underlying token's blacklist check causes this to revert.
5. Because there is no try/catch in `handlePostRequestTimeouts`, the entire transaction reverts — none of the other users' legitimate timeouts are processed, and Alice's request remains available to poison the next batch attempt indefinitely. [4](#0-3)

### Citations

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L344-365)
```text
    function onPostRequestTimeout(PostRequestTimeout calldata incoming) external override onlyHost whenNotPaused {
        HyperFungibleToken.Message memory message = abi.decode(incoming.request.body, (HyperFungibleToken.Message));
        address refundee = _toAddr(message.from);

        if (_isWeth) {
            // Try a native-ETH push first; if the refundee cannot accept native value
            // (e.g. the caller used the ERC-20 deposit path in `send()` from a
            // non-payable contract), re-wrap the withdrawn ETH and deliver the
            // underlying WETH as an ERC-20 transfer so the timeout still settles and
            // funds are not permanently locked.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = refundee.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(refundee, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(refundee, message.amount);
        }

        emit Refunded({to: refundee, amount: message.amount});
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleTokenUpgradeable.sol (L368-390)
```text
    function onPostRequestTimeout(PostRequestTimeout calldata incoming) external override onlyHost whenNotPaused {
        HyperFungibleTokenUpgradeable.Message memory message =
            abi.decode(incoming.request.body, (HyperFungibleTokenUpgradeable.Message));
        address refundee = _toAddr(message.from);

        if (_isWeth) {
            // Try a native-ETH push first; if the refundee cannot accept native value
            // (e.g. the caller used the ERC-20 deposit path in `send()` from a
            // non-payable contract), re-wrap the withdrawn ETH and deliver the
            // underlying WETH as an ERC-20 transfer so the timeout still settles and
            // funds are not permanently locked.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = refundee.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(refundee, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(refundee, message.amount);
        }

        emit Refunded({to: refundee, amount: message.amount});
    }
```

**File:** evm/src/core/HandlerV2.sol (L254-286)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            PostRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            // known request? also serves as source check
            bytes32 requestCommitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(requestCommitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(REQUEST_RECEIPTS_STORAGE_PREFIX, requestCommitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(PostRequestTimeout(request, _msgSender()), meta, requestCommitment);
        }
    }
```
