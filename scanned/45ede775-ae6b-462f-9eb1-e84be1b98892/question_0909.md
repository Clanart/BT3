# Q909: EVM ENearProxy mint asset-branch confusion on finalization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/ENearProxy.sol::mint` ends up accepting two inconsistent interpretations of the same economic event specifically around `asset-branch confusion on finalization` under fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
