# Q951: EVM OmniBridge native/bridged branching native versus wrapped branch switch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM init/finalize entrypoints` and then replay or reorder the complementary outbound or inbound bridge leg so that `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped branch switch` under selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
