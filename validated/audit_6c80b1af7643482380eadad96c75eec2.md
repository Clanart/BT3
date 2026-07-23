The bug is confirmed in the code. Let me trace the exact path:

**`transferProviderOwnership`** updates `providerOwner[provider]` and the `_providersByCreator` sets, but never touches `isUpdater`: [1](#0-0) 

**`_requireUpdater`** passes if `isUpdater[provider][msg.sender]` is true — independent of current ownership: [2](#0-1) 

**`setConfidence`** calls `_requireUpdater` then directly calls `setConfidenceParam` on the provider: [3](#0-2) 

`confidenceParam` directly scales the oracle spread into the bid/ask prices returned to the pool: [4](#0-3) 

The same pattern exists in `AnchoredProviderFactory.sol`: [5](#0-4) 

The existing test `testOldOwnerCannotUpdateAfterTransfer` only tests the case where the old owner did **not** pre-grant themselves as updater — it does not cover the self-grant-then-transfer path: [6](#0-5) 

---

### Title
Ex-Owner Retains Updater Privilege After Ownership Transfer, Enabling Persistent `confidenceParam` Manipulation — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`)

### Summary
`transferProviderOwnership` does not clear the `isUpdater` mapping for the previous owner. An address that granted itself updater rights before transferring ownership retains the ability to call `setConfidence` indefinitely, manipulating the bid-ask spread on pools that use the provider.

### Finding Description
`PriceProviderFactory.transferProviderOwnership` updates `providerOwner[provider]` and the `_providersByCreator` enumerable sets, but leaves `isUpdater[provider][previousOwner]` untouched. Because `_requireUpdater` accepts any address where `isUpdater[provider][msg.sender] == true`, the ex-owner bypasses the ownership check entirely. The attack sequence is:

1. Attacker calls `createPriceProvider(...)` — permissionless, becomes owner.
2. Attacker calls `grantUpdater(provider, attacker)` — sets `isUpdater[provider][attacker] = true`.
3. Attacker calls `transferProviderOwnership(provider, victim)` — `providerOwner[provider]` becomes `victim`, but `isUpdater[provider][attacker]` remains `true`.
4. Attacker calls `factory.setConfidence([provider], [value])` — `_requireUpdater` passes because `isUpdater[provider][attacker]` is still `true`; `PriceProvider.setConfidenceParam` is invoked.

The same flaw exists in `AnchoredProviderFactory.sol` at the same `transferProviderOwnership` / `setConfidence` pair.

### Impact Explanation
`confidenceParam` scales the oracle's raw spread before it is applied to compute bid and ask prices returned to the pool via `getBidAndAskPrice`. Widening `confidenceParam` toward `CONFIDENCE_MAX` lowers the bid and raises the ask, causing every swap against that pool to execute at a worse price than the oracle mid warrants — a direct bad-price execution impact on traders. Narrowing it to zero collapses the spread to only `marginStep`, potentially allowing arbitrageurs to extract value from the pool. The attacker can repeat this every `CONFIDENCE_COOLDOWN` interval for as long as the new owner has not explicitly called `revokeUpdater`.

### Likelihood Explanation
The provider creation flow is permissionless. Any address can create a provider, self-grant as updater, and transfer ownership. The new owner has no on-chain signal that a stale updater entry exists; the `isUpdater` mapping is not surfaced in the ownership-transfer event. The new owner must proactively enumerate and revoke all prior updaters, which is not enforced or documented.

### Recommendation
In `transferProviderOwnership`, explicitly revoke the previous owner's updater entry:

```solidity
// inside transferProviderOwnership, before updating providerOwner:
if (isUpdater[provider][previousOwner]) {
    isUpdater[provider][previousOwner] = false;
    emit UpdaterRevoked(provider, previousOwner);
}
```

Alternatively, require a two-step ownership acceptance so the incoming owner can audit and revoke updaters before taking control.

### Proof of Concept
```solidity
function testExOwnerRetainsUpdaterAfterTransfer() public {
    vm.warp(100);
    address p = _create(FEED_A);          // owner creates provider

    factory.grantUpdater(p, owner);        // owner self-grants as updater

    factory.transferProviderOwnership(p, creatorB); // transfer to new owner

    // owner is no longer providerOwner[p], but isUpdater[p][owner] == true
    address[] memory providers = new address[](1);
    providers[0] = p;
    uint256[] memory values = new uint256[](1);
    values[0] = 900_000;                   // widen spread aggressively

    // This should revert but does NOT — ex-owner still passes _requireUpdater
    factory.setConfidence(providers, values);
    assertEq(PriceProvider(p).confidenceParam(), 900_000);
}
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L34-37)
```text
    function _requireUpdater(address provider) internal view {
        if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
            revert NotProviderUpdater();
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L92-102)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L130-142)
```text
    function setConfidence(
        address[] calldata providers,
        uint256[] calldata values
    ) external override {
        uint256 l = providers.length;
        if (l != values.length) revert LengthMismatch();

        for (uint256 i; i < l; ++i) {
            require(_providers.contains(providers[i]), ProviderNotTracked());
            _requireUpdater(providers[i]);
            PriceProvider(providers[i]).setConfidenceParam(values[i]);
        }
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L137-141)
```text
    function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
        uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
        bid = delta >= midPrice ? 0 : midPrice - delta;
        ask = midPrice + delta;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L230-256)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }

    // ── Updater management ────────────────────────────────────────────
    // Updaters may tune the quote-shaping knobs of customizable providers (batch setters below)
    // but can NOT swap sources — setSource stays owner-only.

    function grantUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = true;
        emit UpdaterGranted(provider, updater);
    }

    function revokeUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = false;
        emit UpdaterRevoked(provider, updater);
    }
```

**File:** smart-contracts-poc/test/PriceProviderFactory.t.sol (L322-333)
```text
    function testOldOwnerCannotUpdateAfterTransfer() public {
        address p = _create(FEED_A);
        factory.transferProviderOwnership(p, creatorB);

        address[] memory providers = new address[](1);
        providers[0] = p;
        uint256[] memory values = new uint256[](1);
        values[0] = 500_000;

        vm.expectRevert(IPriceProviderFactory.NotProviderUpdater.selector);
        factory.setConfidence(providers, values);
    }
```
