Let me analyze the external bug and search for nearcore analogs. The core issue is: a function makes a direct interface call on an address that may not implement the interface (EOA), causing a revert instead of graceful handling.

I'll look for nearcore patterns where a function assumes a certain capability/interface on an address/account that may not have it.