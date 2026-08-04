## Title
Missing `request.from` authentication in `BandwidthManager.onAccept` allows any hyperbridge-chain module to set arbitrary tier prices - (File: `evm/src/apps/BandwidthManager.sol`)

### Summary
`BandwidthManager.onAccept` is documented as accepting governance messages "exclusively from `pallet-bandwidth`" [1](#0-0) , and it identifies that pallet via the constant `PALLET_BANDWIDTH_MODULE_ID = bytes("BWMARKET")` [2](#0-1) . However, the function only validates `msg.sender == host()` (via `onlyHost`) and that `request.source` equals the hyperbridge chain id — it never checks `request.from` against `PALLET_BANDWIDTH_MODULE_ID`:

```solidity
function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
    PostRequest calldata request = incoming.request;
    if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();
    OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
    if (action == OnAcceptActions.SetTiers) {
        ...
``` [3](#0-2) 

### Finding Description
`PostRequest` carries a distinct `source` (the source *chain* id) and `from` (the source *application/module* address/id on that chain) [4](#0-3) . The Host routes an incoming POST purely by chain id and the `to` field (destination app address on this chain) — it does not restrict which module identity on the source chain can address a given destination app. The `onlyHost` modifier only proves the message came through the local Hyperbridge Host contract [5](#0-4) , and `request.source.equals(hyperbridge())` only proves the message originated from the hyperbridge relay chain in general — not that it was dispatched by `pallet-bandwidth` specifically.

Any pallet/module on the hyperbridge chain that can dispatch an ISMP POST request to `BandwidthManager`'s address on this EVM chain, with an arbitrary `from` value, will pass both checks and reach the `SetTiers` branch, allowing it to overwrite `tierPrice[tier]` for any tier — an action the contract's own documentation states is reserved exclusively for `pallet-bandwidth`.

### Impact Explanation
This breaks the invariant "only `pallet-bandwidth` may set `tierPrice`." An attacker-controlled or third-party module on the hyperbridge chain could set tier prices to zero or an arbitrary low value, letting purchasers buy bandwidth for far less than intended (loss of treasury revenue / under-payment), or set prices arbitrarily high to grief legitimate purchases. Because `Withdraw` uses the same unauthenticated `onAccept` entrypoint, the same missing `from` check would also let an unauthorized hyperbridge-chain module trigger `Withdraw`, draining the contract's fee-token or native balance to an arbitrary beneficiary — an unauthorized transaction/fund-movement impact matching the bounty's "unauthorized transaction or execution" and "stealing or loss of funds" categories [6](#0-5) .

### Likelihood Explanation
Exploitability depends on whether an unprivileged/attacker-controlled module can actually get the Host to deliver a POST with `to = BandwidthManager` and forged `from`. Within the scope of this file and its documented trust model, no additional binding of `from` to `PALLET_BANDWIDTH_MODULE_ID` exists in `onAccept`, so the check is genuinely absent at the application layer. Whether the ISMP Host/router elsewhere in the pallet system additionally restricts which modules may address arbitrary destination `to` values on other chains was not verifiable from the files inspected in this repo pass — that routing/authorization logic lives in the substrate pallet/router side, which was outside what I could confirm here. Given the code comment explicitly asserts pallet-only trust and the check is missing exactly where the report claims, this is a valid, concrete code defect regardless of whether other layers partially mitigate it.

### Recommendation
Add an explicit check in `onAccept` that `request.from.equals(PALLET_BANDWIDTH_MODULE_ID)` (in addition to the existing `source` check) before processing `SetTiers` or `Withdraw`, e.g.:
```solidity
if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();
if (!request.from.equals(PALLET_BANDWIDTH_MODULE_ID)) revert UnauthorizedAction();
```

### Proof of Concept
1. Construct an `IncomingPostRequest` where `request.source == IDispatcher(_host).hyperbridge()`, `request.from = bytes("EVIL")` (anything other than `"BWMARKET"`), `request.to = address(bandwidthManager)`, `request.body = abi.encodePacked(uint8(OnAcceptActions.SetTiers), abi.encode(updates))`.
2. Call `bandwidthManager.onAccept(incoming)` from the mocked/real `_host` address (satisfying `onlyHost`).
3. Observe `tierPrice[tier]` is updated to the attacker-chosen value and `TierSet` is emitted, despite `from` not being `PALLET_BANDWIDTH_MODULE_ID` — confirming the missing authentication of the message sender identity, matching the `onAccept` code at [3](#0-2) .

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L59-63)
```text
/// @title BandwidthManager
/// @notice Per-chain prepaid bandwidth storefront. Buyers call
/// `purchase()` to debit a fee-token and dispatch a credit message to
/// `pallet-bandwidth` on hyperbridge; tier prices and treasury
/// withdrawals are governed exclusively by the pallet via `onAccept`.
```

**File:** evm/src/apps/BandwidthManager.sol (L68-71)
```text
    /// Must equal `pallet-bandwidth`'s `PalletId`. The pallet enforces
    /// this on inbound messages, so changing it on either side breaks
    /// the round-trip.
    bytes public constant PALLET_BANDWIDTH_MODULE_ID = bytes("BWMARKET");
```

**File:** evm/src/apps/BandwidthManager.sol (L201-212)
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
```

**File:** evm/src/apps/BandwidthManager.sol (L213-221)
```text
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

**File:** sdk/packages/core/contracts/libraries/Message.sol (L40-55)
```text
struct PostRequest {
    /// @notice Source chain identifier (e.g., "POLKADOT-1000", "EVM-1")
    bytes source;
    /// @notice Destination chain identifier
    bytes dest;
    /// @notice Unique nonce for this request on the source chain
    uint64 nonce;
    /// @notice Source application address that initiated this request
    bytes from;
    /// @notice Destination application address to receive this request
    bytes to;
    /// @notice Unix timestamp when this request expires
    uint64 timeoutTimestamp;
    /// @notice Request payload to be delivered to the destination
    bytes body;
}
```

**File:** sdk/packages/core/contracts/apps/HyperApp.sol (L59-62)
```text
    modifier onlyHost() {
        if (msg.sender != host()) revert UnauthorizedCall();
        _;
    }
```
