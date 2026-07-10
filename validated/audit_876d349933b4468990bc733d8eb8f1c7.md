### Title
Unpaused `ENearProxy.finaliseNearToEthTransfer` with FakeProver Allows Unauthorized eNEAR Minting — (`evm/src/eNear/contracts/ENearProxy.sol`)

---

### Summary

`ENearProxy` is designed to act as the sole authorized minter of eNEAR tokens by replacing the legacy RainbowBridge proof path with a `FakeProver` that accepts any proof. The contract exposes a public `finaliseNearToEthTransfer()` function gated only by a `PAUSED_LEGACY_FIN_TRANSFER` flag. Because `initialize()` never sets that flag, the function is **unpaused by default**. Any unprivileged caller can invoke it with attacker-crafted proof bytes, pass the always-true `FakeProver`, and cause `ENearProxy` (acting as eNear admin) to mint arbitrary eNEAR to any address — with no MPC signature, no bridge nonce, and no locked collateral on NEAR.

---

### Finding Description

**Architecture intent (from README):**

> "We will make `eNearProxy` the admin of `eNear` and replace the `Prover` with a `FakeProver` that will successfully verify any proof. We will pause the `finaliseNearToEthTransfer` and `transferToNear` functions, and only `eNearProxy`, as the admin, will have the ability to call these functions."

The intended security model is:
1. eNear's own `finaliseNearToEthTransfer` is paused for regular users.
2. Only ENearProxy (as admin) can call it.
3. ENearProxy's own legacy path (`finaliseNearToEthTransfer`) is supposed to be paused.

**Root cause — missing default pause in `initialize()`:** [1](#0-0) 

The `initialize()` function sets up roles and stores the `FakeProver` reference but **never calls `_pause(PAUSED_LEGACY_FIN_TRANSFER)`**. The default value of the pause bitmask is `0` (unpaused).

**The unprotected public entry point:** [2](#0-1) 

`finaliseNearToEthTransfer()` is `external`, gated only by `whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER)`. Since that flag is never set in `initialize()`, this function is callable by anyone immediately after deployment.

**The FakeProver always returns `true`:** [3](#0-2) 

`prover.proveOutcome(proofData, proofBlockHeight)` unconditionally returns `true` for any input. The proof check is meaningless.

**ENearProxy (as admin) bypasses eNear's own pause:**

The test suite confirms that even when eNear's `finaliseNearToEthTransfer` is paused via `adminPause(PAUSE_FINALISE_FROM_NEAR)`, ENearProxy (as admin) can still call it successfully: [4](#0-3) 

So the attacker's call to `ENearProxy.finaliseNearToEthTransfer()` propagates through to `eNear.finaliseNearToEthTransfer()` with full admin privileges.

**The proof data format is publicly documented in `mint()`:** [5](#0-4) 

The exact Borsh encoding used to craft a valid-looking proof (recipient address + amount) is visible in the `mint()` function. An attacker can replicate this encoding with their own address and any amount.

**The deployment script never pauses the legacy path:** [6](#0-5) 

`deploy-e-near-proxy` deploys the proxy and exits. No `pauseAll()` or `pause(PAUSED_LEGACY_FIN_TRANSFER)` call follows.

---

### Impact Explanation

An attacker can mint an unbounded amount of eNEAR tokens to any address without:
- Locking any NEAR on the NEAR side.
- Providing a valid MPC signature.
- Going through `OmniBridge.finTransfer()`.

This directly inflates eNEAR supply with no backing collateral, constituting **unauthorized mint of bridged assets** and breaking bridge collateralization. The attacker can then sell or redeem the unbacked eNEAR, draining real value from the protocol.

Impact class: **Critical — Direct unauthorized mint of native bridged assets on EVM.**

---

### Likelihood Explanation

- The `FakeProver` is a production deployment artifact (deployed via `deploy-fake-prover` task, referenced in mainnet/testnet deployment docs).
- The `initialize()` omission is structural — it is not a race condition or a transient misconfiguration window; the function remains callable indefinitely unless an admin manually calls `pauseAll()` or `pause(PAUSED_LEGACY_FIN_TRANSFER)` post-deployment.
- The proof encoding is fully reverse-engineerable from the public `mint()` source.
- No privileged access, leaked key, or colluding party is required.

Likelihood: **High.**

---

### Recommendation

Pause the legacy path atomically during initialization:

```solidity
function initialize(
    address _eNear,
    address _prover,
    bytes memory _nearConnector,
    uint256 _currentReceiptId,
    address _adminAddress
) public initializer {
    __UUPSUpgradeable_init();
    __AccessControl_init();
    __Pausable_init();
    eNear = IENear(_eNear);
    nearConnector = _nearConnector;
    currentReceiptId = _currentReceiptId;
    prover = INearProver(_prover);
    _grantRole(DEFAULT_ADMIN_ROLE, _adminAddress);
    _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    _pause(PAUSED_LEGACY_FIN_TRANSFER); // ← add this
}
```

This ensures the legacy `finaliseNearToEthTransfer` path is disabled from the moment of deployment, regardless of whether the admin remembers to call `pauseAll()` afterward.

---

### Proof of Concept

```solidity
// Attacker crafts proof bytes matching the format in ENearProxy.mint()
// (recipient = attacker, amount = 1_000_000 ether)
bytes memory craftedProof = abi.encodePacked(
    new bytes(72),
    hex"01000000",
    uint256(0),          // currentReceiptId (any value)
    new bytes(24),
    Borsh.swapBytes4(uint32(nearConnector.length)),
    nearConnector,
    hex"022500000000",
    Borsh.swapBytes16(uint128(1_000_000 ether)),
    attacker,            // recipient address
    new bytes(280)
);

// ENearProxy.finaliseNearToEthTransfer is unpaused by default.
// FakeProver.proveOutcome() returns true for any input.
// ENearProxy (as eNear admin) bypasses eNear's own pause.
eNearProxy.finaliseNearToEthTransfer(craftedProof, 0);

// Result: attacker holds 1_000_000 eNEAR with zero NEAR locked on NEAR side.
assert(eNear.balanceOf(attacker) == 1_000_000 ether);
```

### Citations

**File:** evm/src/eNear/contracts/ENearProxy.sol (L33-49)
```text
    function initialize(
        address _eNear,
        address _prover,
        bytes memory _nearConnector,
        uint256 _currentReceiptId,
        address _adminAddress
    ) public initializer {
        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init();
        eNear = IENear(_eNear);
        nearConnector = _nearConnector;
        currentReceiptId = _currentReceiptId;
        prover = INearProver(_prover);
        _grantRole(DEFAULT_ADMIN_ROLE, _adminAddress);
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
```

**File:** evm/src/eNear/contracts/ENearProxy.sol (L51-73)
```text
    function mint(
        address token,
        address to,
        uint128 amount
    ) public onlyRole(MINTER_ROLE) {
        require(token == address(eNear), "ERR_INCORRECT_ENEAR_ADDRESS");

        bytes memory fakeProofData = bytes.concat(
            new bytes(72),
            hex"01000000",
            abi.encodePacked(currentReceiptId),
            new bytes(24),
            abi.encodePacked(Borsh.swapBytes4(uint32(nearConnector.length))),
            abi.encodePacked(nearConnector),
            hex"022500000000",
            abi.encodePacked(Borsh.swapBytes16(amount)),
            abi.encodePacked(to),
            new bytes(280)
        );

        currentReceiptId += 1;
        eNear.finaliseNearToEthTransfer(fakeProofData, 0);
    }
```

**File:** evm/src/eNear/contracts/ENearProxy.sol (L80-90)
```text
    function finaliseNearToEthTransfer(
        bytes memory proofData,
        uint64 proofBlockHeight
    ) external whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER) {
        require(
            prover.proveOutcome(proofData, proofBlockHeight),
            "Proof should be valid"
        );

        eNear.finaliseNearToEthTransfer(proofData, proofBlockHeight);
    }
```

**File:** evm/src/eNear/contracts/FakeProver.sol (L1-9)
```text
// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity 0.8.24;

import {INearProver} from "./IENear.sol";

contract FakeProver is INearProver {
    function proveOutcome(bytes calldata, uint64) external pure returns (bool) {
        return true;
    }
```

**File:** evm/tests/eNearProxy.test.ts (L126-141)
```typescript
    it("Pause All", async () => {
      await eNear.connect(eNearAdmin).adminPause(PAUSE_TRANSFER_TO_NEAR | PAUSE_FINALISE_FROM_NEAR)

      await eNearProxy
        .connect(deployer)
        .grantRole(ethers.keccak256(ethers.toUtf8Bytes("MINTER_ROLE")), alice.address)
      await expect(eNearProxy.connect(alice).mint(await eNear.getAddress(), alice.address, 100)).to
        .be.reverted
      expect(await eNear.balanceOf(alice.address)).to.equal(0)

      await eNear
        .connect(eNearAdmin)
        .adminSstore(9, ethers.zeroPadValue(await eNearProxy.getAddress(), 32))
      await eNearProxy.connect(alice).mint(await eNear.getAddress(), alice.address, 100)
      expect(await eNear.balanceOf(alice.address)).to.equal(100)
    })
```

**File:** evm/src/eNear/scripts.ts (L4-33)
```typescript
task("deploy-e-near-proxy", "Deploys the ENearProxy contract")
  .addParam("enear", "Address of eNear contract")
  .addParam("prover", "Address of prover contract")
  .addParam("admin", "Admin of the proxy contract")
  .setAction(async (taskArgs, hre: HardhatRuntimeEnvironment) => {
    const { ethers, upgrades } = hre

    const eNear = await ethers.getContractAt("IENear", taskArgs.enear)
    const nearConnector = await eNear.nearConnector()

    const eNearProxyContract = await ethers.getContractFactory("ENearProxy")
    const eNearProxy = await upgrades.deployProxy(
      eNearProxyContract,
      [taskArgs.enear, taskArgs.prover, nearConnector, 0, taskArgs.admin],
      {
        initializer: "initialize",
        timeout: 0,
      },
    )

    await eNearProxy.waitForDeployment()
    const proxyAddress = await eNearProxy.getAddress()
    const implementationAddress = await upgrades.erc1967.getImplementationAddress(proxyAddress)
    console.log(
      JSON.stringify({
        proxyAddress,
        implementationAddress,
      }),
    )
  })
```
