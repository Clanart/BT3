### Title
Funds sent to WSEI via `SELFDESTRUCT` become permanently stuck because the internal `balanceOf` mapping is not updated - (File: contracts/src/WSEI.sol)

### Summary
`WSEI.sol`, Sei's canonical wrapped-native (WSEI) contract deployed on-chain via `CmdDeployWSEI` [1](#0-0) , tracks ownership of the wrapped native balance in an internal `balanceOf` mapping rather than relying on `address(this).balance` for accounting [2](#0-1) . Any native value forced into the contract without going through `deposit()` (invoked by `receive()`/`fallback()`) increases `address(this).balance` but never credits any address's `balanceOf`, so those funds can never be withdrawn by anyone.

### Finding Description
`WSEI.sol` implements the classic WETH9-style pattern: `deposit()` credits `balanceOf[msg.sender]` with `msg.value`, and `withdraw()` requires `balanceOf[msg.sender] >= wad` before decrementing the mapping and transferring out [3](#0-2) . The `receive()`/`fallback()` handlers route ordinary value-only calls through `deposit()`, so plain sends are correctly accounted for [4](#0-3) .

However, `SELFDESTRUCT` transfers a contract's balance to its beneficiary without invoking the beneficiary's code at all (no `receive()`/`fallback()`/`deposit()` call occurs). Sei's EVM explicitly implements this standard EVM opcode behavior, confirmed by the in-repo `SelfDestructTester`/`SelfDestructFactory` contracts and their documented semantics under the "prague" EVM version (EIP-6780): calling `destroy(recipient)` on a previously deployed contract sweeps its balance to `recipient` as a pure balance transfer [5](#0-4) . Any unprivileged user can deploy a small contract, fund it, and call `selfdestruct(WSEI_ADDRESS)`, which increases `address(this).balance` of the WSEI contract but leaves every account's `balanceOf` entry in `WSEI.sol` unchanged, since `deposit()`/`receive()`/`fallback()` are never executed on this path.

Because `withdraw()` is gated exclusively on the `balanceOf` mapping and not on the actual native balance held by the contract [6](#0-5) , the value delivered via `SELFDESTRUCT` becomes permanently unreachable — no address has a `balanceOf` entry large enough to withdraw it, and there is no admin/rescue function in the contract.

### Impact Explanation
This matches the "funds stuck forever" class from the referenced report: nobody can ever withdraw the value sent this way because the accounting ledger (`balanceOf`) never reflects it, while the underlying native balance does. Since `WSEI.sol` is deployed as the officially provided wrapped-Sei token contract (via `CmdDeployWSEI`, embedding `x/evm/artifacts/wsei/WSEI.bin`) and is likely to be widely integrated as the canonical WSEI used by DeFi protocols (e.g., Uniswap-style routers reference WETH-equivalent contracts as seen in test fixtures) [7](#0-6) , permanently frozen funds represent a direct, unrecoverable loss for whoever sends value this way, and pollutes the contract's `totalSupply()` (which reads `address(this).balance`) relative to the sum of all `balanceOf` entries, causing a permanent accounting mismatch.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a user (attacker or by accident) to explicitly deploy a helper contract and call `SELFDESTRUCT` targeting the WSEI address, rather than doing an ordinary transfer (which is correctly handled via `receive()`). This is fully reachable by any unprivileged EVM transaction sender with no special privileges, and doesn't require validator or governance cooperation.

### Recommendation
Do not rely solely on the `balanceOf` mapping for `withdraw()`/`totalSupply()` semantics; alternatively add a sweep/rescue mechanism (e.g., allow the difference between `address(this).balance` and the sum of tracked balances to be reconciled or withdrawn by design, similar to a `skim()` function), or explicitly document that `WSEI.sol` should never be the target of a `SELFDESTRUCT` and add tests to confirm production integrations never rely on `address(this).balance` matching `balanceOf` totals.

### Proof of Concept
1. Deploy `WSEI.sol` (already available/deployable via `seid tx evm deploy-wsei`).
2. Deploy a small helper contract with a payable constructor and a `function kill(address payable to) external { selfdestruct(to); }`.
3. Fund the helper contract with native SEI (e.g., 1 SEI) and call `kill(WSEI_ADDRESS)`.
4. Observe that `address(this).balance` of the WSEI contract increases by 1 SEI worth of wei, but no address's `balanceOf` entry changes and `totalSupply()` now permanently exceeds the sum of all `balanceOf` values by that amount; no existing holder can withdraw the extra balance because `withdraw()` is gated by `balanceOf[msg.sender]`.

### Citations

**File:** x/evm/client/cli/tx.go (L428-463)
```go
func CmdDeployWSEI() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "deploy-wsei --from=<sender> --gas-fee-cap=<cap> --gas-limt=<limit> --evm-rpc=<url>",
		Short: "Deploy ERC20 contract for a native Sei token",
		Long:  "",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) (err error) {
			contractData := wsei.GetBin()

			key, err := getPrivateKey(cmd)
			if err != nil {
				return err
			}

			rpc, err := cmd.Flags().GetString(FlagRPC)
			if err != nil {
				return err
			}
			var nonce uint64
			if n, err := cmd.Flags().GetInt64(FlagNonce); err == nil && n >= 0 {
				nonce = uint64(n)
			} else {
				nonce, err = getNonce(rpc, key.PublicKey)
				if err != nil {
					return err
				}
			}

			txData, err := getTxData(cmd)
			if err != nil {
				return err
			}
			txData.Nonce = nonce
			txData.Value = utils.Big0
			txData.Data = contractData

```

**File:** contracts/src/WSEI.sol (L14-32)
```text
    mapping (address => uint)                       public  balanceOf;
    mapping (address => mapping (address => uint))  public  allowance;

    fallback() external payable {
        deposit();
    }
    receive() external payable {
        deposit();
    }
    function deposit() public payable {
        balanceOf[msg.sender] += msg.value;
        emit Deposit(msg.sender, msg.value);
    }
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```

**File:** contracts/src/SelfDestructTester.sol (L12-58)
```text
 *
 * Note on EIP-6780 (active under the "prague" EVM version configured for this
 * repo): SELFDESTRUCT only fully deletes the account (code + storage) when it
 * is executed in the SAME transaction that created the contract. When called in
 * a later transaction it merely transfers the contract's balance and leaves the
 * code and storage in place. Both paths are exercised by the tests:
 *   - destroy() on a previously-deployed instance  -> balance sweep only
 *   - SelfDestructFactory.createAndDestroy(...)     -> full account cleanup
 */
contract SelfDestructTester {
    address public owner;
    uint256 public valueA;
    uint256 public valueB;
    // mapping storage so there are multiple distinct storage slots to clean up
    mapping(uint256 => uint256) public data;
    uint256 public numKeys;

    event Destroyed(address indexed recipient, uint256 balance);

    // Constructor seeds storage so a freshly-created contract has state to clean
    // up. Payable so the contract can hold a balance that selfdestruct sweeps.
    constructor(uint256 _numKeys) payable {
        owner = msg.sender;
        valueA = 0xA11CE;
        valueB = 0xB0B;
        numKeys = _numKeys;
        for (uint256 i = 0; i < _numKeys; i++) {
            // non-zero values so the slots are actually written
            data[i] = i + 1;
        }
    }

    function get(uint256 key) external view returns (uint256) {
        return data[key];
    }

    // Sum the seeded keys — handy for asserting storage is intact pre-destruct.
    function sumKeys() external view returns (uint256 total) {
        for (uint256 i = 0; i < numKeys; i++) {
            total += data[i];
        }
    }

    function destroy(address payable recipient) external {
        emit Destroyed(recipient, address(this).balance);
        selfdestruct(recipient);
    }
```

**File:** x/evm/artifacts/wsei/artifacts.go (L1-54)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
)

const CurrentVersion uint16 = 1

//go:embed WSEI.abi
//go:embed WSEI.bin
var f embed.FS

var cachedBin []byte
var cachedABI *abi.ABI

func GetABI() []byte {
	bz, err := f.ReadFile("WSEI.abi")
	if err != nil {
		panic("failed to read WSEI contract ABI")
	}
	return bz
}

func GetParsedABI() *abi.ABI {
	if cachedABI != nil {
		return cachedABI
	}
	parsedABI, err := abi.JSON(strings.NewReader(string(GetABI())))
	if err != nil {
		panic(err)
	}
	cachedABI = &parsedABI
	return cachedABI
}

func GetBin() []byte {
	if cachedBin != nil {
		return cachedBin
	}
	code, err := f.ReadFile("WSEI.bin")
	if err != nil {
		panic("failed to read WSEI contract binary")
	}
	bz, err := hex.DecodeString(string(code))
	if err != nil {
		panic("failed to decode WSEI contract binary")
	}
	cachedBin = bz
	return bz
}
```
