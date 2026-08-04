## Analysis

The external report's core defect is: **funds are transferred to / entrusted to a destination address whose only protection is "hope it has the right receiving logic and only the right receiving logic can move the funds out."** In Hyperbridge, the direct analog is the `CallDispatcher` contract used by `HyperFungibleToken` / `WrappedHyperFungibleToken` (and TokenGateway calldata-execution flow) for "bridge-and-execute" transfers.

**Flow:**
1. A sender teleports tokens with `to = CallDispatcher` and a non-empty `data` field (an ABI-encoded `Call[]`), so that minted/unlocked tokens land at the `CallDispatcher` address before it executes follow-up calls (e.g., approve + swap). [1](#0-0) 

2. `HyperFungibleToken.onAccept` mints tokens to the `beneficiary` decoded from the message, then unconditionally forwards `message.data` to the configured `_dispatcher` via `ICallDispatcher(_dispatcher).dispatch(message.data)`. [2](#0-1) 

3. `CallDispatcher.dispatch()` is a completely **public, unauthenticated** function — it has no `onlyHost`, `restrict`, or ownership check of any kind, unlike every other privileged entrypoint in the codebase (`onAccept` is always `onlyHost`): [3](#0-2) 

The documentation itself confirms `CallDispatcher` is a **shared, singleton deployment** reused across all `HyperFungibleToken`/`WrappedHyperFungibleToken` instances on a chain ("Existing `CallDispatcher` deployments are listed on the contract addresses page"), and that it "holds tokens temporarily during execution." [4](#0-3) 

### The corrupted value / broken invariant

Because `dispatch()` has no access control, **any token balance sitting in `CallDispatcher` at any point in time can be drained by anyone**, not just by the intended `onAccept` call that put it there. This happens whenever:
- The bridged `amount` isn't *exactly* consumed by the accompanying `Call[]` (rounding, partial swap fills, a call array that approves/spends less than the full minted amount), leaving dust or a larger residual balance in `CallDispatcher`.
- Multiple in-flight cross-chain messages route through the same `CallDispatcher` concurrently across relayers, each temporarily topping up its balance before their own `dispatch` calls consume it.

An attacker (fully unprivileged, no relayer/prover/admin role needed) simply calls `CallDispatcher.dispatch(encodedCalls)` directly with a `Call{to: <bridged token>, value: 0, data: transfer(attacker, dispatcherBalance)}` to sweep out any token balance the contract is holding on behalf of a legitimate, still-pending or already-executed bridge operation. The existing guard ("reverts if the target is not a contract or if any of the calls reverts") does nothing to stop this — it only prevents *failed* calls, not *unauthorized callers*.

This is the exact structural analog to the Axelar bug: LiFi's `destinationAddress` needed specific functions to safely receive and route funds, and if wired wrong, funds were unrecoverable; here, `CallDispatcher` is the equivalent "receiving address" that legitimately needs to hold funds transiently, but because its execution function is not access-controlled to the `HyperFungibleToken`/host that deposited the funds, any third party can invoke the same receiving logic to redirect the custodied assets to themselves.

### Title
Unauthenticated `CallDispatcher.dispatch()` allows theft of bridged tokens temporarily held for calldata execution - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher` is the shared, address-reusable contract that `HyperFungibleToken.onAccept` (and `WrappedHyperFungibleToken`) mints/unlocks bridged tokens to before executing bridge-and-execute calldata. Its `dispatch()` function is `external` with no access control, so any account currently holding a token balance in `CallDispatcher` — including residual/undispensed bridged funds — can be drained by an arbitrary unprivileged caller.

### Finding Description
`HyperFungibleToken.onAccept` mints `message.amount` to `beneficiary` and, if `message.data` is non-empty, calls `ICallDispatcher(_dispatcher).dispatch(message.data)` in the same transaction. When applications set `to = CallDispatcher` per the documented "transfer-and-swap" pattern, the bridged tokens land at `CallDispatcher`'s balance and are then supposed to be entirely consumed by the encoded `Call[]`. [1](#0-0) 

`CallDispatcher.dispatch()` itself carries no caller restriction: [3](#0-2) 

Because the contract is a shared singleton across all HFT/WrappedHFT deployments on a chain, its ERC20/ETH balance is not scoped per-message. Any leftover balance — from an incomplete `Call[]` (e.g., approve for less than the full bridged amount, a swap that doesn't consume 100% due to slippage-limited amounts, or a sender simply not fully sweeping in their `data`), or from ETH sent via `receive()` — remains in `CallDispatcher` and is freely spendable by the next arbitrary caller of `dispatch()`.

### Impact Explanation
This directly enables unauthorized transfer of bridged funds: an attacker can call `dispatch()` with a `Call` targeting the residual token's `transfer`/`transferFrom` function to move `CallDispatcher`'s balance to themselves, stealing value that legitimately belongs to bridge recipients. This is a fund-theft/loss vector reachable by any unprivileged address, matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories.

### Likelihood Explanation
No malicious relayer, prover, or admin is required — the attacker only needs to observe (via mempool/on-chain state) that `CallDispatcher` currently holds a nonzero balance of some token and issue a direct call to `dispatch()`. Given the documented pattern explicitly instructs integrators to route funds through this shared, unauthenticated contract, any imperfect calldata sizing (rounding, slippage, partial-fill swaps) creates a window of exploitable residual balance.

### Recommendation
Restrict `CallDispatcher.dispatch()` so it can only be invoked by the specific `HyperFungibleToken`/`WrappedHyperFungibleToken` instance (or `IHost`) that just deposited the funds within the same call context (e.g., pass an explicit expected-balance/owner check, or make `dispatch` only callable via `delegatecall`-free per-message ephemeral contracts / a pull-based, per-message escrow rather than a shared stateful singleton). At minimum, enforce that `dispatch()` sweeps its entire relevant token balance back to a safe, message-bound recipient at the end of execution and add a caller allowlist limited to registered HFT/host contracts.

### Proof of Concept
1. A relayer delivers a legitimate cross-chain HFT transfer with `to = CallDispatcher`, `amount = 1000`, and `data` encoding `Call[]` that only spends `900` of the minted tokens (e.g., an approve+swap with a slippage-bounded amount), leaving `100` tokens sitting in `CallDispatcher`.
2. `onAccept` executes normally: mints `1000` to `CallDispatcher`, calls `dispatch(data)`, all calls succeed, transaction completes — `100` tokens remain in `CallDispatcher`'s balance. [2](#0-1) 
3. Attacker (any EOA) calls `CallDispatcher.dispatch(abi.encode([Call({to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, 100)})]))` directly. [3](#0-2) 
4. Since `dispatch()` has no caller restriction and `token` is a valid contract, the call succeeds and the residual `100` tokens are transferred to the attacker — funds that were meant for the legitimate bridge flow's slippage/refund handling are stolen.

### Citations

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-304)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```
