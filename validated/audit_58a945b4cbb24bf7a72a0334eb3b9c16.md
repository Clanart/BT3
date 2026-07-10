### Title
`BridgeToken.sol` Lacks Storage Gap, Enabling `_systemAddress` Corruption in `HyperliquedBridgeToken` Upon Upgrade — (`evm/src/omni-bridge/contracts/BridgeToken.sol`)

---

### Summary

`BridgeToken` declares three storage variables (`_name`, `_symbol`, `_decimals`) in its own linear storage layout but contains no `__gap` reserve. `HyperliquedBridgeToken` inherits directly from `BridgeToken` and appends `_systemAddress` immediately after those three slots. Because no gap exists in `BridgeToken`, any future upgrade that adds even one new storage variable to `BridgeToken` will shift `_systemAddress` in `HyperliquedBridgeToken` to an uninitialized (zero) slot. A zeroed `_systemAddress` disables the sole authorization guard in `coreReceiveWithData`, allowing any caller to trigger unbacked token minting and cross-chain bridge-out.

---

### Finding Description

**Root cause — `BridgeToken.sol` has no storage gap:**

`BridgeToken` uses OZ v5 upgradeable base contracts (`ERC20Upgradeable`, `Ownable2StepUpgradeable`, `UUPSUpgradeable`, `Initializable`), all of which use EIP-7201 namespaced storage and therefore occupy **zero** linear storage slots. `BridgeToken`'s own linear storage layout is therefore:

| Slot | Variable |
|------|----------|
| 0 | `_name` (string) |
| 1 | `_symbol` (string) |
| 2 | `_decimals` (uint8, packed) | [1](#0-0) 

There is no `__gap` array at the end of `BridgeToken`. Compare with `OmniBridge`, which correctly reserves `uint256[49] private __gap`. [2](#0-1) 

**`HyperliquedBridgeToken` appends `_systemAddress` directly after `BridgeToken`'s slots:**

`HyperliquedBridgeToken` inherits `BridgeToken` and declares `_systemAddress` as its first own storage variable, landing at slot 3. [3](#0-2) 

**The authorization guard depends entirely on `_systemAddress`:**

```solidity
function coreReceiveWithData(...) external override {
    if (msg.sender != _systemAddress) revert NotSystemAddress();
    ...
}
``` [4](#0-3) 

**Upgrade path that triggers the corruption:**

`OmniBridge.upgradeToken()` upgrades any registered bridge token proxy to a new implementation: [5](#0-4) 

If `BridgeToken` is modified in a future version to add one new storage variable after `_decimals` (e.g., a fee field, a metadata hash, a version flag), the recompiled `HyperliquedBridgeToken` will expect `_systemAddress` at slot 4. The proxy's storage still holds the real system address at slot 3 and zero at slot 4. After the upgrade, `_systemAddress` reads as `address(0)`.

---

### Impact Explanation

With `_systemAddress == address(0)`:

1. The guard `msg.sender != address(0)` is always `true` for any real EOA or contract caller — `NotSystemAddress` never reverts.
2. An attacker calls `coreReceiveWithData` with `action = ACTION_INIT_TRANSFER` and an arbitrary `amount`.
3. `_update(address(0), address(this), amount)` executes — in ERC-20, a transfer *from* `address(0)` is a **mint** operation, creating `amount` tokens out of thin air.
4. `IOmniBridgeInitTransfer(owner()).initTransfer(...)` immediately bridges those freshly minted tokens to an attacker-controlled cross-chain address. [6](#0-5) 

This constitutes **unauthorized minting and cross-chain theft of bridged assets** — a Critical-class impact under the allowed scope.

---

### Likelihood Explanation

The trigger requires `DEFAULT_ADMIN_ROLE` to:
1. Add a new storage variable to `BridgeToken`,
2. Recompile `HyperliquedBridgeToken` against the modified base, and
3. Call `upgradeToken` to deploy the new implementation.

This is a realistic scenario during routine protocol evolution (adding a fee field, a version counter, a metadata hash, etc.). The admin need not act maliciously — the corruption is silent and automatic. The absence of a storage gap makes any such upgrade inherently unsafe, and there is no on-chain check that would warn the admin before the upgrade is finalized.

---

### Recommendation

Add a `__gap` array to `BridgeToken` to reserve upgrade headroom for future storage variables:

```solidity
// BridgeToken.sol — after _decimals
uint256[47] private __gap; // 50 total slots reserved for BridgeToken
```

This ensures that new variables added to `BridgeToken` consume gap slots rather than shifting `_systemAddress` in `HyperliquedBridgeToken`. The gap size should be chosen to leave sufficient room (e.g., 47 slots so that `_name` + `_symbol` + `_decimals` + `__gap` = 50 total reserved slots).

Additionally, consider migrating `_systemAddress` in `HyperliquedBridgeToken` to EIP-7201 namespaced storage (as `SelectivePausableUpgradable` already does) to make it upgrade-layout-independent. [7](#0-6) 

---

### Proof of Concept

**Before upgrade (correct layout):**

| Proxy slot | `BridgeToken` impl reads | `HyperliquedBridgeToken` impl reads |
|---|---|---|
| 0 | `_name` | `_name` |
| 1 | `_symbol` | `_symbol` |
| 2 | `_decimals` | `_decimals` |
| 3 | — | `_systemAddress` = `0xSYSTEM` |

**After `BridgeToken` gains one new variable `_newField` at slot 3, proxy upgraded to new `HyperliquedBridgeToken`:**

| Proxy slot | stored value | new `HyperliquedBridgeToken` impl reads |
|---|---|---|
| 0 | `_name` data | `_name` ✓ |
| 1 | `_symbol` data | `_symbol` ✓ |
| 2 | `_decimals` data | `_decimals` ✓ |
| 3 | `0xSYSTEM` | `_newField` (corrupted) |
| 4 | `0x000…000` | `_systemAddress` = **`address(0)`** ← corrupted |

**Exploit call (after upgrade):**
```solidity
// attacker calls directly — no special role needed
HyperliquedBridgeToken(tokenProxy).coreReceiveWithData(
    attacker,
    bytes32(0),
    0,
    1_000_000e18,          // arbitrary large amount
    0,
    abi.encodePacked(
        uint8(1),          // ACTION_INIT_TRANSFER
        abi.encode(uint128(0), "near:attacker.near", "")
    )
);
// Result: 1_000_000e18 tokens minted from address(0) and bridged to attacker
``` [8](#0-7) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L10-19)
```text
contract BridgeToken is
    Initializable,
    UUPSUpgradeable,
    ERC20Upgradeable,
    Ownable2StepUpgradeable,
    IBridgeToken
{
    string internal _name;
    string internal _symbol;
    uint8 internal _decimals;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L559-566)
```text
    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L598-598)
```text
    uint256[49] private __gap;
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L32-35)
```text
contract HyperliquedBridgeToken is BridgeToken, ICoreReceiveWithData {
    using SafeCast for uint256;

    address internal _systemAddress;
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L106-114)
```text
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L123-135)
```text
        } else if (action == ACTION_INIT_TRANSFER) {
            (uint128 fee, string memory recipient, string memory message) = abi
                .decode(tail, (uint128, string, string));
            uint128 amount128 = amount.toUint128();
            _update(_systemAddress, address(this), amount);
            IOmniBridgeInitTransfer(owner()).initTransfer(
                address(this),
                amount128,
                fee,
                0,
                recipient,
                message
            );
```

**File:** evm/src/omni-bridge/contracts/SelectivePausableUpgradable.sol (L21-37)
```text
    struct SelectivePausableStorage {
        uint256 _pausedFlags;
    }

    // keccak256(abi.encode(uint256(keccak256("aurora.SelectivePausable")) - 1)) & ~bytes32(uint256(0xff))
    bytes32 private constant SelectivePausableStorageLocation =
        0x3385e98de875c27690676838324244576ee92c4384629b3b7dd9c0a7c978e200;

    function _getSelectivePausableStorage()
        private
        pure
        returns (SelectivePausableStorage storage $)
    {
        assembly {
            $.slot := SelectivePausableStorageLocation
        }
    }
```
