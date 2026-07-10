### Title
`uint32 wormholeNonce` Exhaustion Permanently DOS All Bridge Operations - (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole` stores a `uint32 wormholeNonce` counter that is incremented on every bridge operation. Because Solidity 0.8+ uses checked arithmetic, once the counter reaches `type(uint32).max` (4,294,967,295), every subsequent call to `initTransfer`, `finTransfer`, `deployToken`, and `logMetadata` reverts permanently. An unprivileged attacker can exhaust the 32-bit space by repeatedly calling the permissionless `logMetadata()` function, which requires no token deposit — only gas and the Wormhole message fee.

---

### Finding Description

`OmniBridgeWormhole` declares:

```solidity
uint32 public wormholeNonce;
``` [1](#0-0) 

This counter is incremented inside four internal extension hooks — `deployTokenExtension`, `logMetadataExtension`, `finTransferExtension`, and `initTransferExtension` — each of which ends with:

```solidity
wormholeNonce++;
``` [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

All four public entry points that trigger these hooks are accessible without any privileged role:

- `logMetadata(address tokenAddress)` — no token transfer required, only `msg.value` for the Wormhole fee (which is zero on many chains).
- `initTransfer(...)` — requires token transfer but is otherwise open.
- `finTransfer(...)` — open to any relayer.
- `deployToken(...)` — open to any caller with a valid MPC signature.

The most dangerous entry point for an attacker is `logMetadata`:

```solidity
function logMetadata(address tokenAddress) external payable {
    ...
    logMetadataExtension(tokenAddress, name, symbol, decimals);
    ...
}
``` [6](#0-5) 

The attacker deploys a minimal ERC20 contract (with `name()`, `symbol()`, `decimals()` returning arbitrary values) and calls `logMetadata` in a loop. Each call publishes a Wormhole message and increments `wormholeNonce`. No access control, no minimum deposit, no rate limiting exists.

In Solidity ≥ 0.8, `wormholeNonce++` is checked arithmetic. When `wormholeNonce == 4,294,967,295`, the next increment reverts with an overflow panic. From that point forward, **every** call to any of the four public bridge functions reverts at the `wormholeNonce++` line, permanently bricking the contract.

---

### Impact Explanation

Once `wormholeNonce` is exhausted:

1. **`initTransfer` / `initTransfer1155`** — users can no longer lock tokens and initiate cross-chain transfers. Funds sent to the contract in the same transaction are reverted, but any tokens already locked in prior transfers cannot generate new outbound messages.
2. **`finTransfer`** — the Wormhole acknowledgement message cannot be published, so the function reverts before minting or releasing tokens to recipients. Funds in transit (already locked on NEAR) become permanently unclaimable on the EVM side.
3. **`deployToken`** — new bridged token deployments are blocked.
4. **`logMetadata`** — metadata propagation is blocked.

The result is **permanent freezing of all bridge operations** and **irrecoverable lock of user funds in transit**, matching the Critical impact tier.

---

### Likelihood Explanation

`uint32` allows 2^32 ≈ 4.29 billion increments. On cheap EVM L2s where `OmniBridgeWormhole` is deployed (Arbitrum, Base, BNB Chain, Polygon):

- Gas per `logMetadata` call: ~60,000–100,000 gas.
- Gas price on Arbitrum: ~0.01–0.1 gwei.
- Cost per call: ~0.000001–0.00001 ETH.
- Total gas cost to exhaust: ~4,300–43,000 ETH.

If the Wormhole message fee is zero (as it is on several chains), the only cost is gas. A well-funded attacker or protocol adversary could execute this over time. The attack is also parallelizable across multiple accounts. The original Hats Protocol audit report explicitly acknowledged this class of attack as "definitely achievable on cheaper L2 networks" for the same 32-bit counter size — the Omni Bridge Wormhole variant has the identical structure.

---

### Recommendation

1. **Widen the counter type** to `uint64` or `uint256`. Since `wormholeNonce` is only used as a Wormhole message deduplication hint (not a security-critical value), a `uint64` counter is sufficient and eliminates the practical exhaustion risk.
2. **Alternatively**, reset or wrap the nonce safely using unchecked arithmetic if the Wormhole protocol permits nonce reuse (the Wormhole `nonce` field is advisory, not a replay-prevention mechanism on the Wormhole side).
3. **Add a minimum fee** for permissionless entry points like `logMetadata` to raise the economic cost of exhaustion attacks.

---

### Proof of Concept

```solidity
// Attacker contract
contract Exploit {
    OmniBridgeWormhole bridge;
    FakeToken token;

    constructor(address _bridge) {
        bridge = OmniBridgeWormhole(_bridge);
        token = new FakeToken(); // ERC20 with name/symbol/decimals
    }

    // Call this in a loop (batched via script) until wormholeNonce == type(uint32).max
    function exhaust(uint256 iterations) external {
        for (uint256 i = 0; i < iterations; i++) {
            bridge.logMetadata{value: 0}(address(token));
        }
    }
}

contract FakeToken {
    function name() external pure returns (string memory) { return "X"; }
    function symbol() external pure returns (string memory) { return "X"; }
    function decimals() external pure returns (uint8) { return 18; }
}
```

After `4,294,967,295` calls, `wormholeNonce` equals `type(uint32).max`. The next call to any bridge function (including legitimate user `initTransfer` or relayer `finTransfer`) reverts with an arithmetic overflow panic at:

```solidity
wormholeNonce++; // reverts permanently
``` [5](#0-4) 

All bridge operations are permanently frozen. Funds in transit on the NEAR side that were awaiting EVM-side finalization via `finTransfer` become irrecoverable.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L30-30)
```text
    uint32 public wormholeNonce;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L69-69)
```text
        wormholeNonce++;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L93-93)
```text
        wormholeNonce++;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L115-115)
```text
        wormholeNonce++;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L149-149)
```text
        wormholeNonce++;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
```
