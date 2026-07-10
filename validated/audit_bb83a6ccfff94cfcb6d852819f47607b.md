### Title
`BridgeToken` Base Contract Missing Storage Gap Causes Storage Collision in `HyperliquedBridgeToken._systemAddress` on Upgrade - (File: evm/src/omni-bridge/contracts/BridgeToken.sol)

### Summary
`BridgeToken` is an upgradeable UUPS base contract that declares three linear storage variables but no `__gap`. `HyperliquedBridgeToken` inherits from it and appends `address internal _systemAddress` immediately after those variables. Any future upgrade that adds a new state variable to `BridgeToken` will overwrite `_systemAddress` in deployed `HyperliquedBridgeToken` proxies, corrupting the critical access-control and accounting variable that governs the HyperCore↔HyperEVM bridge path.

### Finding Description

`BridgeToken` occupies three consecutive storage slots with its own declared variables: [1](#0-0) 

- Slot 0: `string internal _name`
- Slot 1: `string internal _symbol`
- Slot 2: `uint8 internal _decimals`

The contract ends at line 81 with no `__gap` array: [2](#0-1) 

`HyperliquedBridgeToken` inherits `BridgeToken` and immediately appends its own variable, which lands at slot 3: [3](#0-2) 

`_systemAddress` is used in two critical places:

1. **Access control gate** for the HyperCore→HyperEVM callback: [4](#0-3) 

2. **Accounting pivot** for the HyperCore token pool in `mint`: [5](#0-4) 

By contrast, `OmniBridge` — the sibling contract in the same directory — correctly reserves a gap: [6](#0-5) 

The OpenZeppelin base contracts (`ERC20Upgradeable`, `Ownable2StepUpgradeable`) used by `BridgeToken` already use EIP-7201 namespaced storage in OZ v5, so they do not consume linear slots. The only linear slots in `BridgeToken` are the three explicitly declared ones, making slot 3 — occupied by `_systemAddress` — directly vulnerable.

### Impact Explanation

If `BridgeToken` is upgraded with one or more new state variables appended after `_decimals`, those variables will occupy slot 3 onward in every deployed `HyperliquedBridgeToken` proxy, overwriting `_systemAddress` with arbitrary data. Two concrete consequences:

- **Permanent bridge freeze (Critical/High):** `coreReceiveWithData` checks `msg.sender != _systemAddress` and reverts if they differ. A corrupted `_systemAddress` means no HyperCore→HyperEVM callback can ever succeed again, permanently locking any user funds that are in-flight or subsequently sent from HyperCore.
- **Accounting corruption / unauthorized minting (Critical/High):** The 3-arg `mint` path calls `_update(account, _systemAddress, value)` to park tokens at the system address as the HyperCore pool mirror. A corrupted `_systemAddress` redirects minted tokens to an unintended address, breaking bridge collateralization and potentially enabling unbacked supply.

### Likelihood Explanation

The codebase is actively developed and `BridgeToken` is the shared base for all bridge tokens. The developers have already demonstrated awareness of the gap pattern (used in `OmniBridge`), making it plausible that a future `BridgeToken` upgrade adds a variable without realizing the inheritance hazard. The upgrade path is gated by `DEFAULT_ADMIN_ROLE`, but the damage is caused by a legitimate, well-intentioned upgrade — not malicious operator behavior.

### Recommendation

Add a `__gap` array to `BridgeToken` to reserve storage space for future variables, consistent with the pattern already used in `OmniBridge`:

```solidity
// evm/src/omni-bridge/contracts/BridgeToken.sol
// At the end of the contract, before the closing brace:
uint256[47] private __gap; // 50 - 3 used slots = 47
```

The size should account for the three already-used slots (`_name`, `_symbol`, `_decimals`) so the total reserved space equals 50 slots.

### Proof of Concept

1. Deploy `HyperliquedBridgeToken` as a UUPS proxy. `_systemAddress` is stored at storage slot 3.
2. Upgrade `BridgeToken` implementation to add `uint256 public newVar` after `_decimals`.
3. After the upgrade, storage slot 3 of the proxy now holds `newVar` (initialized to 0 or any value written during `reinitialize`), overwriting `_systemAddress`.
4. Call `coreReceiveWithData(...)` from the legitimate HyperLiquid system address — the call reverts with `NotSystemAddress()` because `_systemAddress` is now `address(0)` (or garbage).
5. All HyperCore→HyperEVM bridge transfers are permanently frozen; any tokens parked at the old `_systemAddress` are stranded.

### Citations

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L17-19)
```text
    string internal _name;
    string internal _symbol;
    uint8 internal _decimals;
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L78-81)
```text
    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyOwner {}
}
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L32-36)
```text
contract HyperliquedBridgeToken is BridgeToken, ICoreReceiveWithData {
    using SafeCast for uint256;

    address internal _systemAddress;
    bytes32 constant HYPER_CORE_DEPLOYER_SLOT = keccak256("HyperCore deployer");
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L81-83)
```text
        _mint(account, value);
        _update(account, _systemAddress, value);
    }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L114-114)
```text
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L598-599)
```text
    uint256[49] private __gap;
}
```
