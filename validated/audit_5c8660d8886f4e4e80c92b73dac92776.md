### Title
Permanent Token Lock via Unvalidated `address(this)` Recipient in `ACTION_TRANSFER` — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`coreReceiveWithData` in `HyperliquedBridgeToken` decodes the ACTION_TRANSFER recipient directly from user-supplied `data` with no guard against `recipient == address(this)`. A HyperCore user can encode the token contract's own address as the EVM recipient, causing `_update(_systemAddress, address(this), amount)` to move tokens into the contract's own balance. No sweep, rescue, or release path exists for tokens held at `address(this)`, making the lock permanent.

---

### Finding Description

In `coreReceiveWithData`, the ACTION_TRANSFER branch is:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    _update(_systemAddress, recipient, amount);
}
``` [1](#0-0) 

The only access guard is `msg.sender != _systemAddress`. [2](#0-1) 

The system address is a HyperLiquid relay that faithfully forwards whatever `data` the HyperCore user supplied in `sendToEvmWithData`. There is no on-chain check that `recipient != address(this)`, `recipient != address(0)`, or any other sanity constraint on the decoded address.

The `ACTION_INIT_TRANSFER` branch also moves tokens to `address(this)` temporarily, but only as an intermediate step before `initTransfer` burns them: [3](#0-2) 

That burn path pulls a fresh `amount` from `_systemAddress` — it does not rescue tokens already stranded at `address(this)` from a prior ACTION_TRANSFER call.

`BridgeToken` exposes `burn(address, uint256)` and `mint(address, uint256)` only to `onlyOwner` (the OmniBridge contract), and neither `HyperliquedBridgeToken` nor `BridgeToken` contains any sweep or rescue function: [4](#0-3) 

---

### Impact Explanation

Tokens moved to `address(this)` via ACTION_TRANSFER are permanently irrecoverable:

- No `transfer` the contract can self-initiate.
- No `sweep` or `rescue` function.
- `burn(address(this), ...)` is `onlyOwner` and is never called except inside `initTransfer`, which pulls a separate `amount` from `_systemAddress` — it does not target the stranded balance.
- The `_systemAddress` pool is also depleted by `amount`, creating an accounting drift that can cause future legitimate `ACTION_INIT_TRANSFER` calls to revert with insufficient pool balance.

Impact: **Critical — permanent freezing of bridged assets in the token contract.**

---

### Likelihood Explanation

The attack is fully self-service from HyperCore:

1. Attacker calls `sendToEvmWithData` with EVM recipient = `address(token)` and `data = 0x00 || abi.encode(address(token))`.
2. The HyperLiquid system address relays this faithfully to `coreReceiveWithData`.
3. `_update(_systemAddress, address(token), amount)` executes with no revert.
4. `balanceOf(address(token)) == amount`; no release path exists.

No privileged key, no MPC collusion, no chain-level attack is required. Any HyperCore user with a token balance can trigger this against themselves or against the protocol's `_systemAddress` pool.

---

### Recommendation

Add a recipient sanity check at the top of the ACTION_TRANSFER branch:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    require(
        recipient != address(this) && recipient != address(0),
        "HlBridgeToken: invalid recipient"
    );
    _update(_systemAddress, recipient, amount);
}
``` [1](#0-0) 

---

### Proof of Concept

```solidity
// Precondition: _systemAddress holds >= amount tokens (set by prior 3-arg mint)
// Attacker encodes address(token) as the ACTION_TRANSFER recipient
bytes memory data = abi.encodePacked(
    uint8(0),                        // ACTION_TRANSFER
    abi.encode(address(token))       // recipient = token contract itself
);

// System address calls coreReceiveWithData (relaying attacker's sendToEvmWithData)
vm.prank(systemAddress);
token.coreReceiveWithData(
    attacker,
    bytes32(0),
    0,
    amount,
    0,
    data
);

// Tokens are now permanently locked
assertEq(token.balanceOf(address(token)), amount);
// No function exists to recover them
```

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L114-114)
```text
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L120-122)
```text
        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L127-135)
```text
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

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L62-64)
```text
    function burn(address account, uint256 value) external onlyOwner {
        _burn(account, value);
    }
```
