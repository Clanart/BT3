Let me analyze the external report's core invariant and search for nearcore analogs systematically.

The external bug is: a ratio (`preDepositPrice = underlyingValue / totalSupply`) is computed from volatile external prices with no minimum-output guard, so two callers with identical inputs get different outputs depending on timing.

The nearcore analog to look for: a ratio or price-like value computed from mutable on-chain state (stake, epoch info, part counts, shard assignments) that gates how much of something a user/node receives, with no floor/ceiling check.