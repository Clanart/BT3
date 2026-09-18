### Title
Missing before/after balance verification in `CW20ERC20Pointer` transfer functions allows fee-on-transfer / non-standard CW20 tokens to break ERC-20 transfer-amount guarantees - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer.sol` wraps an arbitrary CosmWasm CW20 contract as a standard Solidity ERC-20 so it can be used by EVM contracts. Its `transfer()` and `transferFrom()` functions fire a CW20 `transfer`/`transfer_from` execute message through the wasmd precompile and unconditionally return `true` without ever checking that the recipient's (or sender's) CW20 balance changed by exactly `amount`. Because any CosmWasm user can deploy and register a pointer for a custom CW20 contract with non-standard transfer semantics (fee-on-transfer, rebasing, partial-transfer-on-insufficient-liquidity, etc.), EVM consumers of the pointer are misled into believing the full `amount` moved when it did not — the same class of bug described in the external report for ERC-20 fee-on-transfer tokens, just crossing the CW20↔ERC20 pointer bridge instead of a raw ERC-20 transfer.

### Finding Description
`transfer` and `transferFrom` in `CW20ERC20Pointer` build a JSON CW20 execute message and call `_execute`, which only checks that the `delegatecall` to the wasmd precompile succeeded — it never inspects the actual amount that moved: [1](#0-0) 

```solidity
function transfer(address to, uint256 amount) public override returns (bool) {
    ...
    _execute(bytes(req));
    return true;
}

function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
    ...
    _execute(bytes(req));
    return true;
}
```

`_execute` only asserts that the CosmWasm call did not revert: [2](#0-1) 

Because the underlying `Cw20Address` contract is arbitrary CosmWasm code supplied by whoever registers the pointer (via `seid tx evm register-cw-pointer CW20 <addr>`, reachable by any CosmWasm user/pointer user), it is not guaranteed to move exactly `amount` tokens on a `transfer`/`transfer_from` call. A CW20 contract could legitimately implement a transfer tax, a burn-on-transfer mechanism, or any other logic that debits the sender but credits the recipient with less than `amount`. The pointer contract has no way to detect this: it does not query `balanceOf` before and after the CosmWasm call, so it always reports success and (via the standard `ERC20` machinery / synthetic events) implies a 1:1 transfer.

This is architecturally the same root cause the external report flags for `RizLendingPool`/`RizLeverager`/etc.: code that trusts a `transferFrom`-style call to move the exact requested amount without verifying balances before and after.

### Impact Explanation
Any EVM smart contract built on Sei that treats `CW20ERC20Pointer` as a conformant ERC-20 (e.g., a DEX pool, lending market, or vault that credits a depositor based on the `amount` parameter passed to `transferFrom`, assuming standard ERC-20 semantics) can be under-collateralized or over-credit a counterparty if the wrapped CW20 token deducts a fee or otherwise moves a different amount than requested. This can result in direct fund loss/mismatched accounting for protocols composing on top of CW20 pointers, without any privileged action — the attacker only needs to deploy a custom CW20 contract and get (or already have) a pointer registered for it, then interact with a downstream protocol that trusts the pointer's ERC-20 interface.

### Likelihood Explanation
Deploying a custom CW20 contract and registering a pointer for it is a normal, permissionless CosmWasm user action (`register-cw-pointer CW20`), and pointers are a first-class, documented bridging mechanism intended for arbitrary CW20 tokens. Any downstream EVM integration that composes with CW20 pointers without independently verifying balances is exposed. Likelihood is moderate: it requires a downstream protocol to trust the pointer's return value/emitted semantics rather than checking balances itself, but the sei-chain protocol code itself provides no protection against non-conformant CW20 tokens routed through this bridge.

### Recommendation
In `CW20ERC20Pointer.transfer` and `transferFrom`, query `balanceOf(to)` (and `balanceOf(from)`/`balanceOf(msg.sender)` as applicable) both before and after the `_execute` call to the CW20 contract, and revert if the observed delta does not match the requested `amount`. This mirrors the recommendation in the original report and would prevent the pointer from silently misrepresenting non-standard CW20 transfer behavior to EVM consumers.

### Proof of Concept
1. Deploy a CW20 contract on Sei whose `transfer`/`transfer_from` handlers deduct a percentage fee (e.g., burn 5% of every transfer) — this is ordinary, permissionless CosmWasm contract deployment.
2. Register an ERC-20 pointer for this CW20 contract via `seid tx evm register-cw-pointer CW20 <cw20_addr>` (see `registerPointerForERC20`/`registerPointerForCw` helper at [3](#0-2) ).
3. From an EVM contract, call `pointer.transferFrom(victim, attacker, 1000)` after obtaining allowance; the pointer's CW20 `transfer_from` message actually moves only 950 tokens to `attacker` (fee burned), yet `CW20ERC20Pointer.transferFrom` returns `true` unconditionally.
4. Any downstream EVM contract that credited the attacker with `1000` tokens based on the pointer's return value/`amount` parameter (rather than re-checking `balanceOf`) now has inflated/incorrect accounting relative to the actual CW20 balance movement.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L79-96)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```

**File:** contracts/test/lib.js (L825-838)
```javascript
async function registerPointerForCw(cwAddress, type, fees, from) {
    const command = `seid tx evm register-cw-pointer ${type} ${cwAddress} --from ${from} --fees ${fees} -b sync -y -o json`
    const response = JSON.parse(await execute(command))
    if (response.code !== 0) throw new Error(`contract deployment failed: ${response.raw_log}`)
    let pointer = ''
    try {
        await waitForCondition(
            async () => {
                const out = await execute(`seid query evm pointer ${type} ${cwAddress} -o json`)
                pointer = JSON.parse(out).pointer || ''
                return pointer !== ''
            },
            `pointer for ${type} ${cwAddress}`,
        )
```
