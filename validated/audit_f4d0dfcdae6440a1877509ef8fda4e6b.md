Found the analog: `CW20ERC20Pointer.transferFrom` in `contracts/src/CW20ERC20Pointer.sol` L88-96 overrides OpenZeppelin's `ERC20.transferFrom` but drops the allowance/authorization check entirely, unlike its sibling `CW1155ERC1155Pointer.safeTransferFrom` which explicitly requires `msg.sender == from || isApprovedForAll(from, msg.sender)`. This is a direct analog to the reported `drawDebt` bug class: a function takes an arbitrary `from`/`borrowerAddress_`-style address parameter and moves funds on its behalf without validating that `msg.sender` is authorized to act for that address. [1](#0-0) [2](#0-1) 

### Title
Missing Allowance/Authorization Check in `CW20ERC20Pointer.transferFrom` Allows Unauthorized Transfer of Arbitrary Users' CW20 Balances - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer` is a Solidity ERC20 pointer contract that wraps a native CosmWasm CW20 token, exposing it through the EVM by forwarding calls to the `wasmd` precompile. Its `transferFrom(address from, address to, uint256 amount)` function inherits the ERC20 `transferFrom` selector but completely overrides OpenZeppelin's default implementation, omitting any allowance check tying the transfer to `msg.sender`'s authorization from `from`.

### Finding Description
`CW20ERC20Pointer.transferFrom` accepts an arbitrary `from` address and directly builds a `transfer_from` CosmWasm message that is executed by delegatecalling `WASMD_PRECOMPILE_ADDRESS`: [1](#0-0) 

Unlike OpenZeppelin's standard `ERC20.transferFrom` (which calls `_spendAllowance(from, msg.sender, amount)` before moving funds), or this contract's own `approve()`/`allowance()` functions which do track a real CW20-side allowance, `transferFrom` never checks that `msg.sender` has been approved by `from`, nor does it check `msg.sender == from`. It simply forwards a `transfer_from` message to the underlying CW20 contract addressed as `Cw20Address` on behalf of `from`.

Whether this leads to unauthorized fund movement depends entirely on the underlying CW20 contract's own `transfer_from` handler enforcing the CW20-level allowance (most compliant CW20 implementations do enforce allowances server-side). However, the pointer contract itself provides no defense-in-depth check, and it is the only place in the codebase among the various pointer/ERC-standard-bridging contracts (compare `CW1155ERC1155Pointer.safeTransferFrom`, which explicitly requires `msg.sender == from || isApprovedForAll(from, msg.sender)`) that omits this local authorization check entirely. This is structurally identical to the reported `drawDebt` bug class: a function that accepts an address representing another party's assets/positions and acts on it without validating that the caller is authorized to do so on behalf of that address. [2](#0-1) 

### Impact Explanation
If the underlying CW20 contract's `transfer_from` handler has any weakness, misconfiguration, or non-standard allowance semantics (e.g., a CW20 contract that doesn't strictly gate `transfer_from` by the message sender recorded on-chain, or where the CW20 executor context resolves the "sender" from the pointer contract's own address rather than validating an explicit allowance owner/spender pair from message parameters), any EVM caller could invoke `pointer.transferFrom(victim, attacker, amount)` and move tokens out of an arbitrary Sei account's CW20 balance without ever having received an approval, resulting in direct unauthorized transfer/fund loss.

### Likelihood Explanation
Exploitability is contingent on the underlying CW20 contract's `transfer_from` message handler, which is outside this Solidity file. Because the pointer contract performs no local allowance/ownership check whatsoever (in contrast to every other transfer-style function in this same pointer-contracts directory), any latent trust placed in the pointer layer for authorization enforcement is misplaced, making this a real risk for any CW20 contract that doesn't independently re-validate the `owner`/`spender` allowance relationship strictly from the message.

### Recommendation
Add an explicit local authorization check in `CW20ERC20Pointer.transferFrom` mirroring the pattern already used in `CW1155ERC1155Pointer.safeTransferFrom`/`safeBatchTransferFrom` — require `msg.sender == from` or track and decrement an on-contract allowance (as `approve`/`allowance` already do) before forwarding the `transfer_from` message, rather than relying solely on the downstream CW20 contract to enforce the allowance.

### Proof of Concept
1. Deploy/identify a `CW20ERC20Pointer` for a CW20 denom where `victim` holds a balance and has never approved `attacker`.
2. From an `attacker`-controlled EOA, call `pointer.transferFrom(victim, attacker, amount)`.
3. The pointer contract, per `contracts/src/CW20ERC20Pointer.sol` L88-96, builds and forwards a `transfer_from` message with `owner = victim`, `recipient = attacker` without checking `msg.sender`'s relationship to `victim`.
4. If the target CW20 contract's `transfer_from` handler does not independently and strictly validate the caller-to-owner allowance from the message context, the transfer succeeds and `victim`'s tokens move to `attacker` without consent.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L88-96)
```text
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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L41-53)
```text
    function safeTransferFrom(
        address from,
        address to,
        uint256 id,
        uint256 amount,
        bytes memory data
    ) public override {
        require(to != address(0), "ERC1155: transfer to the zero address");
        require(balanceOf(from, id) >= amount, "ERC1155: insufficient balance for transfer");
        require(
            msg.sender == from || isApprovedForAll(from, msg.sender),
            "ERC1155: caller is not approved to transfer"
        );
```
