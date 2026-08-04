## Analysis

The Tempo Bug (ToB) finding's core broken invariant: a contract designed as a generic message/notification relay accepts and executes an **attacker-crafted arbitrary payload without verifying that the payload matches its intended purpose**, and — critically — the relay itself carries no restriction on *who* may trigger it, only on *what* it forwards. The recommended fix was "implement a dedicated notification contract... isolat[e] this functionality" with proper caller restriction.

Hyperbridge's EVM SDK has a structurally identical component: `CallDispatcher`, a generic execution relay shared by `HyperFungibleToken`, `WrappedHyperFungibleToken`, their upgradeable variants, and `IntentGatewayV2`. It is invoked from `onAccept` after minting/transferring value directly to it, with **zero caller authentication** on its public entrypoint.### Title
Unauthenticated `CallDispatcher.dispatch()` allows any attacker to drain value en route to cross-chain recipients - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
The ToB finding's broken invariant is: a message relay trusted by a token master contract (the pool) accepts and forwards attacker-crafted payloads to an arbitrary receiver with no restriction on *who* triggers the relay or *what* the payload does, letting an attacker fabricate a privileged message (a fake `internal_transfer`) and mint unearned tokens. Hyperbridge's EVM SDK contains a structurally identical unguarded relay: `CallDispatcher`, used by `HyperFungibleToken`, `WrappedHyperFungibleToken` (and their upgradeable variants) to execute calldata after minting/transferring bridged value to it.

### Finding Description
`CallDispatcher.dispatch()` has no caller restriction whatsoever: [1](#0-0) 

It also has an unrestricted `receive()` that accepts ETH from anyone: [2](#0-1) 

The documented and intended usage pattern is to mint bridged tokens **directly to the `CallDispatcher` address** on `onAccept`, then invoke `dispatch()` with attacker-supplied `Call[]` calldata (approve + swap) so the dispatcher can spend the tokens it was just minted: [3](#0-2) 

The production contracts implement this exactly: `onAccept` mints to `beneficiary` (which can be set to the shared `CallDispatcher` address by any sender on any connected chain), and then unconditionally calls `ICallDispatcher(_dispatcher).dispatch(message.data)`: [4](#0-3) 

The same pattern is repeated in `WrappedHyperFungibleToken.onAccept` and both upgradeable variants: [5](#0-4) [6](#0-5) 

Because `_dispatcher` is a **single shared, stateless, unauthenticated executor** used by multiple independent apps and multiple independent cross-chain messages, any value that transiently resides in it — minted tokens awaiting the composed `Call[]` to fully consume them, native ETH sent via `receive()`, ERC20 dust left over from a partially-consumed swap (e.g. `amount != minAmountOut` slippage, `approve` leaving unspent allowance/balance, or a caller who crafts `message.data` that mints to the dispatcher but supplies calls that don't fully sweep the balance) — is completely exposed. Since `dispatch()` performs no check on `msg.sender`, **any unrelated third party** can call `CallDispatcher.dispatch()` directly with their own `Call[]` targeting the dispatcher's current token/ETH balance and sweep it to themselves, exactly as the ToB bug allowed an attacker to fabricate a privileged message to a trusted address because the relay never validated the caller or the semantic intent of the forwarded payload.

This is not a hypothetical "malicious peer/relayer" precondition — it requires only an ordinary unprivileged EVM account observing that value sits at the `CallDispatcher` address (a publicly known, documented, and reused address across every `HyperFungibleToken`/`WrappedHyperFungibleToken`/`IntentGatewayV2` deployment sharing it) and front-running or racing the legitimate follow-up `dispatch()` call, or simply exploiting slippage/dust left behind after a legitimate call completes.

### Impact Explanation
This falls squarely under "stealing or loss of funds" and "unauthorized execution" in the bounty scope: bridged tokens/ETH intended for a specific cross-chain recipient can be permanently diverted to an unrelated attacker because the shared execution relay that custodies them mid-flight enforces no access control at all. Given `CallDispatcher` is meant to be a single reusable infrastructure contract referenced by name/address in documentation and deployment scripts, the blast radius spans every app that uses it.

### Likelihood Explanation
High for any usage pattern that follows the documented "mint-to-dispatcher-then-swap" flow: any user bridging with calldata that doesn't perfectly consume 100% of the minted amount (routine with slippage-bounded swaps) leaves attacker-claimable balance in a public, unauthenticated contract. No relayer collusion, governance action, or malicious peer is required — the vulnerability is purely a missing-access-control defect in `CallDispatcher.dispatch()`.

### Recommendation
Restrict `CallDispatcher.dispatch()` so it can only be invoked by the specific `IApp` contract that owns the funds currently held by the dispatcher for that call (e.g., pass/verify the calling app as `msg.sender` and scope execution per-app, or make the dispatcher non-shared/ephemeral per call so it never custodies value outside of the single atomic transaction that funded it). At minimum, require `dispatch()` to be called only by an authorized set of registered `IApp` addresses, and ensure any residual balance is swept back to the originating app or reverted rather than left claimable by the public.

### Proof of Concept
1. Any user calls `HyperFungibleToken.send()` on chain A with `to = CALL_DISPATCHER` and `data` encoding a `Call[]` that approves and swaps only part of the minted amount via a router with generous slippage (`minAmountOut` set to allow partial consumption), or a swap that reverts on one leg but not before some tokens/ETH already sit in `CALL_DISPATCHER` from a previous message in the same block.
2. On chain B, `onAccept` mints `amount` tokens to `CALL_DISPATCHER` (`sdk/packages/core/contracts/apps/HyperFungibleToken.sol:299-300`) then calls `ICallDispatcher(_dispatcher).dispatch(message.data)` (line 303), which only spends part of the minted balance, leaving dust/residual tokens sitting at the `CallDispatcher` address.
3. An attacker monitoring `CallDispatcher`'s token/ETH balance calls `CallDispatcher.dispatch()` directly (no permission required — `evm/src/utils/CallDispatcher.sol:44-62`) with a `Call[]` transferring the residual balance to their own address.
4. The attacker successfully drains value that was intended for the legitimate bridge recipient/protocol, with no cryptographic or role-based check preventing them, since `dispatch()` never validates `msg.sender`.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
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

**File:** docs/content/developers/evm/hyper-fungible-token/hyper-fungible-token.mdx (L149-162)
```text
IHyperFungibleToken(tokenAddress).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(42161),
        // mint to the CallDispatcher so the swap can spend the tokens
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```

Tokens are minted to `to` first, then the `CallDispatcher` executes each call in sequence. If the calls need to spend the minted tokens, set `to` to the `CallDispatcher` address so tokens are minted directly to the dispatcher.
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-312)
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

        emit Received({
            from: message.from,
            to: beneficiary,
            source: string(request.source),
            amount: message.amount
        });
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-312)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-336)
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

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```
