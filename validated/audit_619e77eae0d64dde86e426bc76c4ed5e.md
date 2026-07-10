Looking at the external report's vulnerability class — **asset-accounting bug where tokens returned by a function are not fully tracked** — I need to find an analog in Omni Bridge where a bridge operation returns or mints tokens that are not fully accounted for in the bridge's internal accounting.

Let me examine the key production files systematically.