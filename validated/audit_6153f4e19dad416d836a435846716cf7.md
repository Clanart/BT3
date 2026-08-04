### Title
`SimplexPaymaster` caps only `paymasterPostOpGasLimit`, leaving `paymasterVerificationGasLimit` unbounded and drainable via EntryPoint's unused-gas penalty - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
The Nouns DAO bug let a voter inflate a gas-accounting window (via an unbounded `reason` string emitted inside the refund-measured region) so that `REFUND_BASE_GAS`'s fixed assumption under-covered the real cost, draining ETH held by the contract to a caller-chosen recipient. `SimplexPaymaster` has the exact same broken invariant on one of its two attacker-supplied gas-limit fields: it explicitly caps `paymasterPostOpGasLimit` to guard against the ERC-4337 EntryPoint's unused-gas penalty draining the paymaster's deposit, but never applies an equivalent cap to `paymasterVerificationGasLimit`, which is packed into the same `paymasterAndData` and read by the very same EntryPoint penalty mechanism.

### Finding Description
`SimplexPaymaster._validatePaymasterUserOp` only validates `postOpGasLimit`: [1](#0-0) 

The contract's own doc comment states the exact threat model it is trying to close: an unbounded gas-limit field lets "the EntryPoint's unused-gas penalty" be "drained from this contract's deposit to a caller-chosen beneficiary": [2](#0-1) 

`MAX_POST_OP_GAS_LIMIT` is enforced only against `userOp.paymasterPostOpGasLimit()`. There is no corresponding check against `userOp.paymasterVerificationGasLimit()` anywhere in the contract — `_fetchDetails` and `_executePermit` never read or bound it either: [3](#0-2) 

Per ERC-4337 (v0.7/v0.8), `paymasterVerificationGasLimit` is a caller-declared field packed by whoever builds `paymasterAndData` — not something the paymaster contract sets. It is passed to `handleOps`/`handleAggregatedOps`, which is a permissionless, unauthenticated entrypoint that anyone can call directly with themselves as `beneficiary`. The EntryPoint charges a penalty on the unused portion of the declared verification/post-op gas limits against the prefund (here, sponsored entirely from `SimplexPaymaster`'s own EntryPoint deposit, since this is an ERC-20 paymaster sponsoring gas), and credits that penalty to the caller-chosen `beneficiary`. Since the actual verification work performed by `_validatePaymasterUserOp`/`_fetchDetails`/`_executePermit` is small and bounded, but the declared `paymasterVerificationGasLimit` can be set arbitrarily large (e.g. millions of gas) by the UserOp submitter, the gap between declared and actually-used gas is exactly the quantity the developer already identified as an ETH-draining vector for the postOp field — except left open here.

The SDK-side helper functions (`buildSimplexPaymasterData`, `buildPermitMode`) always set sane fixed values for `paymasterVerificationGasLimit` (`VERIFICATION_GAS_LIMIT_PERMIT`/`VERIFICATION_GAS_LIMIT_APPROVE`), but nothing on-chain enforces this — an attacker does not need to go through the SDK at all; they can call `entryPoint.handleOps()` directly with a self-crafted `PackedUserOperation` whose `paymasterAndData` contains an inflated `paymasterVerificationGasLimit`, as long as the `paymasterData` payload itself still passes the mode/token checks in `_fetchDetails`/`_validatePaymasterUserOp` (e.g. using approve mode 0x01 with a pre-approved token and a tiny/zero prefund). [4](#0-3) 

### Impact Explanation
`SimplexPaymaster`'s EntryPoint deposit (funded by governance and by `swapAndDeposit`'s protocol fee recycling) is drained to an attacker-chosen beneficiary through the EntryPoint's unused-gas penalty mechanism, with no cap analogous to `MAX_POST_OP_GAS_LIMIT` blocking the equivalent path on `paymasterVerificationGasLimit`. This is direct loss of protocol-held funds (the paymaster's native EntryPoint deposit) to an arbitrary, unprivileged, self-chosen recipient — matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" criteria, without requiring a malicious relayer, prover, or admin.

### Likelihood Explanation
Likelihood is high for any unprivileged attacker: they only need (a) a registered/active token they can either approve to the paymaster or self-permit (approve mode 0x01 requires just a pre-existing allowance, even of 0/negligible amount if `maxCost` ends up small), and (b) the ability to call `entryPoint.handleOps()` directly, which is permissionless. No relayer, governance, or oracle compromise is needed — only the standard ERC-4337 EntryPoint, which this contract is designed to integrate with.

### Recommendation
Enforce a `MAX_PAYMASTER_VERIFICATION_GAS_LIMIT` cap on `userOp.paymasterVerificationGasLimit()` inside `_validatePaymasterUserOp`, symmetric to the existing `MAX_POST_OP_GAS_LIMIT` check on `postOpGasLimit`, reverting with a dedicated error (e.g. `InvalidVerificationGasLimit`) when exceeded.

### Proof of Concept
1. Attacker selects a token registered/active in `tokenConfigs` (e.g. USDC) and either holds a tiny pre-approved allowance to `SimplexPaymaster` or simply uses approve mode `0x01` with `data = abi.encodePacked(uint8(1), token)`.
2. Attacker builds a `PackedUserOperation` where `paymasterAndData` is packed as `paymaster (20 bytes) || paymasterVerificationGasLimit (uint128, set to a very large value, e.g. 5_000_000) || paymasterPostOpGasLimit (uint128, ≤ MAX_POST_OP_GAS_LIMIT = 100_000) || paymasterData`, following the layout documented in `evm/tests/foundry/SimplexPaymasterTest.t.sol:700-712`.
3. Attacker calls `entryPoint.handleOps([userOp], attackerAddress)` directly (permissionless entrypoint), setting themselves as `beneficiary`.
4. `_validatePaymasterUserOp` only checks `postOpGasLimit ≤ 100_000` and passes since the attacker kept that field small; `paymasterVerificationGasLimit` is never checked and is accepted as-is.
5. The EntryPoint's actual verification work (small, since `_fetchDetails`/`_executePermit` are lightweight) leaves a large unused-gas delta relative to the declared `paymasterVerificationGasLimit`; the EntryPoint's unused-gas penalty on that delta is charged against `SimplexPaymaster`'s prefund/deposit and paid out to `attackerAddress` as beneficiary.
6. Repeating this drains `SimplexPaymaster`'s EntryPoint deposit over multiple calls, exactly mirroring the mechanism the developer's own comment at lines 108-112 warns about for `postOpGasLimit`, but left unmitigated for `paymasterVerificationGasLimit`.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L27-60)
```text
/// @title  SimplexPaymaster
/// @author Polytope Labs
/// @notice Fully onchain, permissionless ERC-4337 v0.8 paymaster that accepts
///         ERC-20 stablecoins (USDC, USDT, or any token with a Chainlink feed)
///         for gas payment. Deployed behind an ERC1967Proxy and administered
///         exclusively through Hyperbridge governance.
///
/// Modes (byte 0 of paymasterData):
///   0x00  PERMIT  — EIP-2612 permit signature included; the permit is executed
///                    during validation so the subsequent prefund transferFrom
///                    succeeds without a prior onchain approval.
///   0x01  APPROVE — Token must be pre-approved to this paymaster (the path for
///                    tokens without permit support, e.g. BSC stablecoins).
///
/// paymasterData encoding:
///   Mode 0x00 (permit):
///     abi.encodePacked(uint8(0), address(token), uint256(permitAmount),
///                      uint256(deadline), uint8(v), bytes32(r), bytes32(s))
///   Mode 0x01 (approve):
///     abi.encodePacked(uint8(1), address(token))
///
/// Price conversion uses two Chainlink feeds: token/USD and nativeAsset/USD.
/// The markup surplus accumulates in the contract and is withdrawable to the
/// treasury; unused gas is refunded to the sender by PaymasterERC20._postOp.
///
/// @dev Security model. Solvers grant this contract ERC-20 allowances, so a
///      compromise must never translate into large withdrawals from their
///      accounts. There is no privileged key: every administrative action —
///      upgrades, parameter changes, token registry, withdrawals — is an
///      onAccept request authenticated as originating from Hyperbridge
///      governance and delivered by the local host. Clients additionally keep
///      allowances and permit amounts small (a few dollars), bounding exposure
///      to the residual allowance even against a malicious oracle.
contract SimplexPaymaster is Initializable, HyperApp, PaymasterERC20 {
```

**File:** evm/src/utils/SimplexPaymaster.sol (L108-112)
```text
    /// @dev Caps the caller-supplied postOp gas limit. Unbounded, the EntryPoint's
    ///      unused-gas penalty is drained from this contract's deposit to a
    ///      caller-chosen beneficiary; the cap keeps that penalty under the
    ///      `_postOpCost` cushion the user already pays.
    uint256 public constant MAX_POST_OP_GAS_LIMIT = 100_000;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L342-364)
```text
    function _validatePaymasterUserOp(
        PackedUserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 maxCost
    ) internal override returns (bytes memory context, uint256 validationData) {
        uint256 postOpGasLimit = userOp.paymasterPostOpGasLimit();
        if (postOpGasLimit > MAX_POST_OP_GAS_LIMIT) {
            revert InvalidPostOpGasLimit(postOpGasLimit, MAX_POST_OP_GAS_LIMIT);
        }

        bytes calldata data = userOp.paymasterData();
        if (data.length == 0) revert InvalidPaymasterData(0);
        if (uint8(data[0]) == 0x00) {
            if (data.length < 21) revert InvalidPaymasterData(data.length);
            address tokenAddr = address(bytes20(data[1:21]));
            TokenConfig memory cfg = tokenConfigs[tokenAddr];
            if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
            if (!cfg.active) revert TokenNotActive(tokenAddr);
            _executePermit(userOp);
        }

        return super._validatePaymasterUserOp(userOp, userOpHash, maxCost);
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L374-415)
```text
    function _fetchDetails(
        PackedUserOperation calldata userOp,
        bytes32 /* userOpHash */
    ) internal view override returns (uint256 validationData, IERC20 token, uint256 tokenPrice) {
        bytes calldata data = userOp.paymasterData();
        if (data.length < 21) revert InvalidPaymasterData(data.length);

        uint8 mode = uint8(data[0]);
        if (mode > 0x01) revert InvalidMode(mode);

        address tokenAddr = address(bytes20(data[1:21]));

        TokenConfig memory cfg = tokenConfigs[tokenAddr];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
        if (!cfg.active) revert TokenNotActive(tokenAddr);

        tokenPrice = _tokenPrice(cfg);
        token = IERC20(tokenAddr);
        validationData = 0; // no time-range restriction
    }

    /// @dev Parse and execute the EIP-2612 permit from paymasterData.
    ///      Layout: mode(1) + token(20) + permitAmount(32) + deadline(32) + v(1) + r(32) + s(32) = 150 bytes
    function _executePermit(PackedUserOperation calldata userOp) internal {
        bytes calldata data = userOp.paymasterData();
        if (data.length != 150) revert InvalidPaymasterData(data.length);

        address tokenAddr = address(bytes20(data[1:21]));
        uint256 permitAmount = uint256(bytes32(data[21:53]));
        uint256 deadline = uint256(bytes32(data[53:85]));
        uint8 v = uint8(data[85]);
        bytes32 r = bytes32(data[86:118]);
        bytes32 s = bytes32(data[118:150]);

        address owner = userOp.sender;

        try IERC20Permit(tokenAddr).permit(owner, address(this), permitAmount, deadline, v, r, s) {
            emit PermitExecuted(tokenAddr, owner, permitAmount);
        } catch {
            revert PermitFailed(tokenAddr);
        }
    }
```
