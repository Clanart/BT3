### Title
Transfer to unassociated EVM/Sei address permanently reverts in ERC20↔CW20 pointer contracts - ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
The `FootiumPrizeDistributor` bug class — a hard-coded recipient address that the underlying token can no longer service, permanently trapping funds already verified as claimable — has a structural analog in sei-chain's ERC20↔CW20 pointer contracts. `CW20ERC20Pointer.transfer`/`transferFrom` resolve the EVM `to`/`from` argument to a Sei bech32 address via the `AddrPrecompile.getSeiAddr` call before constructing the CW20 execute payload, and that precompile call unconditionally reverts if the target EVM address has never been associated with a Sei address.

### Finding Description
`CW20ERC20Pointer.transfer()` requires `AddrPrecompile.getSeiAddr(to)` to succeed before it can build the `transfer` payload sent to the wrapped CW20 contract: [1](#0-0) 

The `getSeiAddr` precompile method reverts outright — rather than falling back to some canonical/derived address — whenever the given EVM address has no stored Sei association: [2](#0-1) 

This is directly confirmed by both integration and unit-level tests: `getSeiAddr` for an unassociated address always reverts with "execution reverted"/"is not associated" (`precompiles/addr/addr_test.go`-style assertions and the `EVMPrecompileTest.js`/`addr.spec.ts` suites), and pointer-level tests explicitly demonstrate that a `transfer()` call whose recipient is unassociated reverts (`ERC20toCW20PointerTest-backup.js`) or silently fails to move balance (`CW20toERC20PointerTest.js`): [3](#0-2) [4](#0-3) [5](#0-4) 

This is analogous to the reported bug class: the pointer's `transfer`/`transferFrom` logic hard-depends on a specific canonical identity for the recipient (a Sei bech32 address resolvable only through prior association), just as the `FootiumPrizeDistributor` hard-depends on the token accepting the whitelisted address stored in the Merkle root. In both cases, a legitimate token movement to a valid, currently-unsupported address permanently fails at the transfer step even though all business logic (balance/allowance checks, Merkle-proof verification analog) otherwise succeeds.

### Impact Explanation
Any EVM contract or protocol that programmatically computes a recipient address for a pointer-wrapped CW20 token (e.g., an escrow, vesting contract, airdrop/reward distributor, DEX router) and sends tokens to an address that has not yet performed EVM↔Sei association will have that specific `transfer`/`transferFrom` call revert unconditionally. If the calling contract does not special-case this failure (for example, a batched distribution loop, or a contract that assumes ERC20 `transfer` following EIP-20 semantics never reverts for a syntactically valid non-zero address), funds intended for that recipient can become undeliverable through the pointer contract and, depending on the caller's design, effectively stuck in the sending contract or lost from that distribution round. Unlike simple key custody, resolving this requires an out-of-band action (the recipient signing an `associate` message) that the sending protocol/contract cannot force or verify in advance.

### Likelihood Explanation
Likelihood is real but bounded: unlike the original TUSD-style double-entry-point case, which is a permanent action by the token issuer, here the block is resolved as soon as the affected address performs a one-time `associate`/`associatePubKey` call, which is permissionless and requires no protocol-side change. This significantly reduces the "indefinite/≥1 year" freeze bar articulated in the referenced Sherlock guidance, since the recipient (or anyone knowing that address's key) can self-remediate. The scenario is most impactful for contracts/integrations that cannot practically prompt the affected address to associate (e.g., addresses controlled by other contracts, addresses computed off-chain with no key custodian actively monitoring), where the freeze can persist for an extended, unbounded period in practice.

### Recommendation
- Have `CW20ERC20Pointer.transfer`/`transferFrom` (and the ERC721/ERC1155 pointer analogs) detect the unassociated-recipient case and either auto-associate a deterministic Sei address for previously-unassociated EVM addresses (consistent with how native/CW-wrapped-in-ERC20 transfers already handle unlinked wallets, per `ERC20toNativePointerTest.js`'s "should transfer to unlinked address" flow) or surface a distinguishable, documented revert reason so calling contracts can implement retry/queue logic instead of silently losing funds.
- Audit any protocol-level contract built on top of these pointers that performs bulk/programmatic transfers to ensure a single unassociated recipient cannot halt or drop funds for the entire batch.

### Proof of Concept
1. Deploy or use an existing `CW20ERC20Pointer` for a CW20 token (see `contracts/test/CW20toERC20PointerTest.js` setup).
2. Call `pointer.transfer(unassociatedEvmAddress, amount)` from an address holding sufficient pointer balance, where `unassociatedEvmAddress` has never called `associate`/`associatePubKey`.
3. Observe that `AddrPrecompile.getSeiAddr(to)` reverts inside `transfer()`, causing the whole transfer to fail — reproduced by the existing test `"transfer to unassociated address should fail"`: [4](#0-3) 
4. A caller contract that does not special-case this revert (e.g., a batch airdrop or vesting contract) cannot deliver tokens to that recipient until the recipient independently performs an EVM↔Sei association, which the sending contract has no ability to trigger or guarantee.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L79-86)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** precompiles/addr/addr.go (L119-135)
```go
func (p PrecompileExecutor) getSeiAddr(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	seiAddr, found := p.evmKeeper.GetSeiAddress(ctx, args[0].(common.Address))
	if !found {
		metrics.IncrementAssociationError("getSeiAddr", types.NewAssociationMissingErr(args[0].(common.Address).Hex()))
		return nil, 0, fmt.Errorf("EVM address %s is not associated", args[0].(common.Address).Hex())
	}
	ret, err = method.Outputs.Pack(seiAddr.String())
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** contracts/test/EVMPrecompileTest.js (L61-68)
```javascript
        it("Associates successfully", async function () {
            const unassociatedWallet = hre.ethers.Wallet.createRandom();
            try {
                await addr.getSeiAddr(unassociatedWallet.address);
                expect.fail("Expected an error here since we look up an unassociated address");
            } catch (error) {
                expect(error).to.have.property('message').that.includes('execution reverted');
            }
```

**File:** contracts/test/ERC20toCW20PointerTest-backup.js (L102-105)
```javascript
        it("transfer to unassociated address should fail", async function() {
            const unassociatedRecipient = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";
            await expect(pointer.transfer(unassociatedRecipient, 1)).to.be.revertedWithoutReason;
        });
```

**File:** contracts/test/CW20toERC20PointerTest.js (L118-128)
```javascript
                it("transfer to unassociated address should fail", async function() {
                    const unassociatedSeiAddr = "sei1z7qugn2xy4ww0c9nsccftxw592n4xhxccmcf4q";
                    const respBefore = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceBefore = respBefore.data.balance;

                    await executeWasm(pointer,  { transfer: { recipient: unassociatedSeiAddr, amount: "100" } });
                    const respAfter = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceAfter = respAfter.data.balance;

                    expect(balanceAfter).to.equal(balanceBefore);
                });
```
