# Q282: EVM OmniBridge native/bridged branching burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM init/finalize entrypoints` and then replay or reorder the complementary outbound or inbound bridge leg so that `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
