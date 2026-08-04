## Finding

### Title
`HostManager` and `BandwidthManager` accept governance `onAccept` messages without validating the sender module (`request.from`), unlike sibling apps in the same codebase - ([File: evm/src/core/HostManager.sol])

### Summary
`HostManager.onAccept` and `BandwidthManager.onAccept` are the two Hyperbridge governance-relay contracts responsible for triggering `EvmHost.withdraw(...)` and `EvmHost.updateHostParams(...)` (i.e., moving bridge revenue and rewriting the host's trusted parameters). Both only validate that the incoming request's **source chain** equals the configured Hyperbridge chain id — they never validate **which module/pallet on that chain** sent the message (`request.from`). Every other app in this repository that performs privileged state changes from an incoming ISMP message (`HyperFungibleToken`, `ExtrinsicIntents`/`IntentGatewayV2`) explicitly checks `request.from` against a registered peer/module address before acting. This is the exact bug class from the external report generalized to Hyperbridge: a config/binding check that is supposed to tie the privileged control channel to one specific counterparty is missing, so the control channel can be entered from an unintended counterparty.

### Finding Description
`HostManager.onAccept` only checks the chain, not the module: [1](#0-0) 

`BandwidthManager.onAccept` has the identical pattern: [2](#0-1) 

Compare this with `HyperFungibleToken.onAccept`, which checks both `request.source` (via `_supportedChains`) **and** `request.from`: [3](#0-2) 

and `ExtrinsicIntents._authenticate`, which explicitly resolves the expected module for the source chain and compares it to `request.from`: [4](#0-3) 

and `IntentGatewayV2.onAccept`, which calls `authenticate(request)` before honoring privileged `NewDeployment`/`UpdateParams` actions: [5](#0-4) 

On the Substrate side, `IsmpDispatcher::dispatch_request` places the `from` field directly into the outgoing `PostRequest` with no constraint tying it to the calling extrinsic's origin/account — it is whatever byte string the calling pallet/extrinsic chooses to pass in: [6](#0-5) 

The documented, canonical pattern for any pallet wiring itself to Hyperbridge is a user-facing extrinsic that forwards an entire attacker-supplied `DispatchPost` (including `from`) straight into `dispatch_request`: [7](#0-6) 

Because `HostManager`/`BandwidthManager` never verify `request.from`, the only thing standing between an arbitrary Hyperbridge-side sender and a call to `EvmHost.withdraw()` / `EvmHost.updateHostParams()` is (a) that the request's `to` field on the destination chain is the `HostManager`/`BandwidthManager` address, and (b) that the request is proven to have genuinely been dispatched on the Hyperbridge chain — both of which are satisfiable by any account able to dispatch a `PostRequest` with a chosen `to`/`from`/`body`, with no malicious relayer, prover, or admin required (the relayer only needs to honestly relay a real, provable request).

### Impact Explanation
If reachable, this allows unauthorized draining of `EvmHost`'s accrued protocol/relayer revenue (`OnAcceptActions.Withdraw` → `IHostManager(_params.host).withdraw(withdrawParams)` with an attacker-chosen `beneficiary`/`amount`), or unauthorized rewriting of `HostParams` (`consensusClient`, `handler`, `hostManager`, `feeToken`, `challengePeriod`, etc.) via `OnAcceptActions.SetHostParam`. The former is direct fund loss/theft; the latter is false/forced state acceptance and unauthorized control of core bridge trust parameters (consensus client, verification handler) — both squarely in the "stealing funds" / "unauthorized execution" / "false proof or state acceptance" impact categories.

### Likelihood Explanation
This is a genuine gap relative to the codebase's own established security pattern (every comparable `onAccept` implementation validates `request.from`, these two do not), so it is provably a missing guard, not intentional design. However, I could not conclusively confirm, from the pallets currently wired into the production runtime, an unprivileged/user-facing extrinsic that lets an ordinary account freely choose `to`=`HostManager address` and an arbitrary `body` for dispatch to an EVM chain — every production dispatch site I found (`host-executive`, `relayer`, `bandwidth`, `intents-coprocessor`) hardcodes `from: PALLET_ID` and is itself gated by a privileged origin or fixed destination logic. The vulnerability is concretely present in the contract code (missing `from` check), but full exploitability depends on whether any current or future pallet exposes a generically-addressable dispatch path to unprivileged accounts — which the codebase's own documentation (`dispatching.mdx`) shows is a trivial thing to build, and which `pallet_ismp::dispatch_request` does nothing to prevent.

### Recommendation
Add an explicit `request.from` check in `HostManager.onAccept` and `BandwidthManager.onAccept`, mirroring `ExtrinsicIntents._authenticate` / `HyperFungibleToken.onAccept`: compare `request.from` against a configured, immutable expected governance module id (e.g., the `pallet-ismp-host-executive` / `pallet-bandwidth` module id) in addition to checking `request.source`. Do not rely on chain-id-only checks for any contract that can trigger fund transfers or host-parameter rewrites.

### Proof of Concept
Conceptual (cannot be fully demonstrated without confirming a concrete unprivileged dispatch entry point on the live runtime, as noted above):
1. Attacker controls (or finds) any pallet/extrinsic on the Hyperbridge chain that forwards a caller-supplied `DispatchPost` into `pallet_ismp::dispatch_request` (pattern shown in `dispatching.mdx`), letting the attacker set `from` to any byte string, `to` to `HostManager`'s EVM address, `dest` to the target EVM chain, and `body` to `abi.encode(uint8(OnAcceptActions.Withdraw), WithdrawParams{beneficiary: attacker, amount: fullBalance, token: feeToken})`.
2. The request commits normally in `pallet-ismp`'s child trie (a legitimate dispatch, no forged proof needed).
3. A relayer (acting honestly) delivers the request with a valid state proof; the EVM handler verifies the proof and calls `HostManager.onAccept`.
4. `onAccept` checks only `request.source == hyperbridge` at [8](#0-7) , which passes, then decodes the attacker's `Withdraw` action and calls `EvmHost.withdraw` with the attacker's beneficiary/amount.

### Citations

**File:** evm/src/core/HostManager.sol (L95-108)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
```

**File:** evm/src/apps/BandwidthManager.sol (L201-221)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        PostRequest calldata request = incoming.request;

        if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.SetTiers) {
            Tier[] memory updates = abi.decode(request.body[1:], (Tier[]));
            for (uint256 i = 0; i < updates.length; i++) {
                tierPrice[updates[i].tier] = updates[i].price;
                emit TierSet(updates[i].tier, updates[i].price);
            }
        } else if (action == OnAcceptActions.Withdraw) {
            Withdrawal memory w = abi.decode(request.body[1:], (Withdrawal));
            if (w.token != address(0)) {
                IERC20(w.token).safeTransfer(w.beneficiary, w.amount);
            } else {
                (bool sent,) = w.beneficiary.call{value: w.amount}("");
                if (!sent) revert InsufficientNativeToken();
            }
            emit Withdrawn(w.token, w.beneficiary, w.amount);
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-296)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L56-67)
```text
    /**
     * @dev Authenticates an incoming cross-chain post request by verifying that the
     * sender module matches the registered gateway instance for the source chain.
     * Reverts with InvalidInput if the sender address is malformed, or Unauthorized
     * if the sender is not the expected gateway.
     * @param request The incoming post request to authenticate.
     */
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-630)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }

        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-146)
```rust
			DispatchRequest::Post(dispatch_post) => {
				let post = PostRequest {
					source: self.host_state_machine(),
					dest: dispatch_post.dest,
					nonce: self.next_nonce(),
					from: dispatch_post.from,
					to: dispatch_post.to,
					timeout_timestamp: if dispatch_post.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_post.timeout)
					},
					body: dispatch_post.body,
				};
				Request::Post(post)
			},
		};
```

**File:** docs/content/developers/polkadot/dispatching.mdx (L57-76)
```text
```rust showLineNumbers
#[pallet::weight(T::dispatch())]
#[pallet::call_index(0)]
pub fn send_message(
    origin: OriginFor<T>,
    post: DispatchPost,
    fee: T::Balance,
) -> DispatchResultWithPostInfo {
    let signer = ensure_signed(origin)?;
    let dispatcher = pallet_ismp::Pallet::<Runtime>::default();
    let commitment = dispatcher.dispatch_request(
        DispatchRequest::Post(post),
        FeeMetadata {
            payer: signer,
            fee,
        }
    )?;

    Ok(())
}
```
