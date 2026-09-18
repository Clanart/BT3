Based on my investigation, the strongest analog is in `CW20ERC20Pointer.sol`, the production EVM-side ERC20 pointer contract that wraps a CW20 token.

### Title
`CW20ERC20Pointer.transfer`/`transferFrom` mints ERC20 events and returns success without verifying the underlying CW20 amount actually moved, breaking compatibility with fee-on-transfer/deflationary CW20 tokens - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer` exposes a standard ERC20 interface backed by an underlying CW20 contract, reachable by any EVM user/contract calling `transfer`/`transferFrom` on the pointer. The implementation blindly returns `true` after dispatching the CW20 `transfer`/`transfer_from` message, without checking how much value was actually credited to the recipient, analogous to the reported bug class where contracts trust a raw `transfer`/`transferFrom` call to move the full requested amount.

### Finding Description
`transfer()` and `transferFrom()` build a CW20 JSON message and call `_execute()`, which only checks that the delegatecall to the wasmd precompile succeeded, then unconditionally returns `true`: [1](#0-0) 

`_execute` merely checks the wasmd precompile call succeeded (`require(success, ...)`) — it does not inspect or validate the actual amount transferred by the underlying CW20 contract: [2](#0-1) 

If the underlying CW20 contract implements non-standard transfer semantics (e.g., charges a fee on transfer, is deflationary/rebasing, or silently caps the transferred amount instead of reverting on a partial condition), the pointer will still emit success (`transfer`/`transferFrom` returning `true`), and any downstream EVM consumer (DEX pool, lending market, other pointer-consuming contract) that assumes `amount` was fully credited to `to` will operate on an incorrect balance assumption — the same failure mode described in the report where callers assume `transfer`/`transferFrom` moved the exact requested amount because of a bool-return/full-transfer assumption. Since the ERC20 pointer's own `balanceOf`/`totalSupply` are also proxied live to the CW20 contract via `WasmdPrecompile.query` (not cached locally), any accounting done by contracts based on the return value of `transfer`/`transferFrom` (rather than re-querying `balanceOf` before/after) is at risk of desync with actual CW20 balances.

### Impact Explanation
Because the CW20↔ERC20 pointer contracts are a core interoperability primitive (any CW20 token can have a pointer auto-created and be used as if it were an ERC20 by any EVM contract), any EVM contract built on top of this pointer that trusts `transfer`/`transferFrom`'s boolean return value to represent full-amount success can be tricked into crediting/debiting incorrect amounts if the wrapped CW20 has non-standard transfer behavior (fee-on-transfer, capped/partial transfer). This can lead to fund loss or accounting desync in downstream DeFi contracts (e.g., a pool crediting a depositor for the full nominal amount while only a smaller true amount was moved).

### Likelihood Explanation
Likelihood is Medium: it requires a CW20 token with non-standard transfer semantics (fee-on-transfer/deflationary) to be pointed via the standard, permissionless pointer-creation flow, and a downstream integrator to trust the ERC20 return value rather than re-checking balances. Sei/CosmWasm's standard `cw20-base` implementation does not have such behavior, so real-world exploitability depends on adoption of custom CW20 implementations with these properties, which is plausible given pointer creation is permissionless.

### Recommendation
Have `transfer`/`transferFrom` in `CW20ERC20Pointer.sol` query the recipient's CW20 balance before and after `_execute`, and revert (or return `false`) if the observed balance delta does not match the requested `amount`, mirroring the "check balance before and after transfer" recommendation from the referenced report. Alternatively, document explicitly that pointer support is only guaranteed for CW20 tokens with standard, fee-free, non-rebasing transfer semantics, and reject/flag pointer creation for tokens whose `token_info`/known implementation deviates from this.

### Proof of Concept
1. Deploy (or imagine) a CW20 contract with fee-on-transfer behavior, e.g., `transfer` sends `amount * 99%` to the recipient and burns/redirects the remainder, but still returns success.
2. Create a pointer for this CW20 via the standard `AddCW20` pointer-registration flow (permissionless), yielding a `CW20ERC20Pointer` instance.
3. From an EVM contract, call `pointer.transfer(recipient, 1000)`; the call succeeds (`_execute` doesn't revert) and `transfer` returns `true`.
4. Query `pointer.balanceOf(recipient)` before and after: the increase is `990`, not `1000`, yet nothing in the pointer's `transfer` reverted or signaled the shortfall to the caller, matching the "protocol/user might not receive the expected assets" impact pattern from the original report.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L79-109)
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
